"""Kit for agent.computer/v2: version 1's eight checks on a version 2 server, and ``late``.

Everything but what version 2 changes is version 1's kit, loaded from its folder (``agent-computer-v1/driver.py``
and the modules beside it): the same desktop image, fixture, verdicts, loop, reaper and reference. Here:

- the port's server is version 2's (no python, no read, prepare, a step's files), its agent's folder ``FOLDER``, its
  window before the task closed by ``task_given`` (which the definition's ``driver.task_given`` asks Nullius to
  send) or, for the reference, as it starts; every prepare call is recorded in ``verifier/computer-prepare.jsonl``;
- ``late``: one labelled button appears ``LATE_SECONDS`` after the task is given; the agent clicks it. It passes when
  the click is the check ``click``'s, and an observation taken as a step ended, after the button was up, reached the
  agent before the press: it looked again by a step. Its times are the driver's own clock (when the button was up,
  each picture of a step's end, each action's start), the press matched to its action on the desktop's clock.
"""
import atexit
import base64
import importlib.util
import json
import os
import secrets
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.join(os.path.dirname(HERE), "agent-computer-v1")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


from nullius.standard.jsonl import serve

v1 = _load("nullius_kit_computer_v1", os.path.join(V1, "driver.py"))
port, desktop, checks = v1.port, v1.desktop, v1.checks

UNITS = checks.CHECKS + ("late",)
FOLDER = v1.FOLDER                           # the agent's own folder in the desktop
LATE_SECONDS = 3.0                           # late: the button appears this long after the task is given


def prompt(unit, expected):
    if unit == "late":
        return (v1.USE + f"A button labelled {expected['target']} appears on the screen a few seconds after you are "
                         f"given this task. Click it once." + v1.DONE)
    return v1.prompt(unit, expected)


class Server(port.Server):
    """Version 1 kit's admission (each acting call counted against the budget as it comes), with version 2's check."""

    def call(self, rid, params, wire):
        start = time.time()
        name = params.get("name")
        if name not in port.ACTING:
            return super().call(rid, params, wire)

        def reply(call, value, image, error):
            content = []
            if error is not None:
                content.append({"type": "text", "text": str(error)})
            if value is not None:
                content.append({"type": "text", "text": json.dumps(value)})
            if image is not None:
                content.append({"type": "image", "data": base64.b64encode(image).decode(), "mimeType": "image/png"})
            data, over = port.bounded({"jsonrpc": "2.0", "id": rid, "result": {"content": content,
                                                                                "isError": error is not None}})
            self.record({"tool": call.tool, "start": call.start, "end": time.time(), "isError": error is not None or over})
            wire.send(data)
            wire.answered()

        with self.bridge.change:
            wire.opened()
            call = port.Call(name, params.get("arguments", {}), reply)
            call.start = start
            if self.bridge.booked >= self.bridge.steps:
                self.bridge.past += 1
            try:
                port.checked(call, self.bridge.screen, self.bridge.folder)
            except port.Refused as e:
                call.answer(error=str(e))
                return
            except Exception as e:
                call.answer(error=f"the arguments could not be checked: {type(e).__name__}")
                return
            self.bridge.submit(call)


class Pictures(v1.KitDesktop):
    """The unit's desktop, with each step's picture timed on the driver's clock."""

    def __init__(self, d, unit):
        super().__init__(d)
        self.unit = unit

    def capture(self):
        t = time.monotonic()
        png = super().capture()
        if self.unit.bridge.running is not None:
            self.unit.pictures.append((t, time.monotonic()))
        return png


class Unit(v1.Unit):
    """Version 1's unit on a version 2 server; ``late`` draws its button once the task is given."""

    def __init__(self, unit, dir):
        super().__init__("click" if unit == "late" else unit, dir)
        if unit == "late":                           # the click screen with one button, drawn later
            label = secrets.SystemRandom().choice(checks.LABELS)
            self.unit, self.steps = unit, checks.steps(unit)
            self.values, self.expected = {"screen": "click", "labels": [label]}, {"target": label, "labels": [label]}
        self.prepared = os.path.join(self.trial, "verifier", "computer-prepare.jsonl")
        self.pictures, self.acted, self.shown = [], [], None
        self.given, self.up, self.ending = threading.Event(), threading.Event(), threading.Event()

    def start(self, job):
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        for path in (self.log, self.prepared):
            if os.path.exists(path):
                os.remove(path)
        image = desktop.image(os.path.dirname(self.log))
        name = f"nullius-desktop-{os.urandom(6).hex()}"
        v1.REAPER.tell(f"+container {name}")
        self.desktop = desktop.Desktop.start(image, name, job=job)
        v1.REAPER.tell(f"=container {name}")
        acting = self.desktop.act

        def act(code, seconds=v1.PYTHON_SECONDS):           # each action's start on the driver's clock too
            t = time.monotonic()
            r = acting(code, seconds)
            if r.get("start") is not None:
                self.acted.append((t, r["start"], r["end"]))
            return r
        self.desktop.act = act
        if self.unit != "late":
            self.desktop.start_fixture(self.values)
            self.boxes = self.desktop.events()[0].get("boxes") or {}
        folder = os.path.join(tempfile.gettempdir(), f"nullius-computer-{os.urandom(6).hex()}")
        v1.REAPER.tell(f"+folder {folder}")
        self.folder = folder
        os.mkdir(folder, 0o700)
        self.bridge = v1.KitBridge(Pictures(self.desktop, self), self.steps, v1.PAUSE, desktop.SCREEN, FOLDER,
                                   window=True, prepared=self.prepared)
        self.server = Server(self.bridge, self.socket(), log=self.log)
        self.loop = threading.Thread(target=self.run_loop, name=f"kit-loop-{self.unit}", daemon=True)
        self.loop.start()
        if self.unit == "late":
            threading.Thread(target=self.late, name="kit-late", daemon=True).start()

    def task_given(self):
        self.bridge.close_window()
        self.given.set()

    def late(self):
        """The button drawn ``LATE_SECONDS`` after the task is given, unless the unit ends first."""
        while not self.given.wait(0.5):
            if self.ending.is_set():
                return
        if self.ending.wait(LATE_SECONDS):
            return
        try:
            self.desktop.start_fixture(self.values)
            self.boxes = self.desktop.events()[0].get("boxes") or {}
            self.shown = time.monotonic()
        except Exception as e:                       # the unit says so at score
            self.error = f"the button could not be drawn: {type(e).__name__}: {e}"
        finally:
            self.up.set()

    def end(self, wait=v1.LOOP_SECONDS):
        self.ending.set()
        super().end(wait)

    def events(self):
        """The fixture's log; none for ``late`` while its button was never drawn."""
        if self.unit == "late" and self.shown is None and not self.boxes:
            return []
        return self.desktop.events()


def late_verdict(u, events, actions, message):
    """``late``: the click's verdict, and an observation of the button, taken as a step ended, before the press."""
    v = checks.click(u.expected, events, actions, message)
    if not v["passed"]:
        return v
    if u.shown is None:
        return {"passed": False, "why": "the button was never drawn"}
    press = next(e for e in events if e.get("kind") == "press" and e.get("hit") == u.expected["target"]
                 and e.get("button") == 1)
    sent = next((h for h, s, e in u.acted if s <= press["t"] <= e + checks.SLACK), None)
    if sent is None or not any(start > u.shown and end < sent for start, end in u.pictures):
        return {"passed": False, "why": "no step's observation showed the button before the press: it was pressed "
                                        "without looking again by a step"}
    return {"passed": True, "why": ""}


def units(dataset, **_):
    return {"units": list(UNITS)}


def setup(unit, dir, **_):
    if unit not in UNITS:
        raise ValueError(f"no unit {unit!r}")
    v1.close_all(shutdown=False)
    u = Unit(unit, dir)
    with v1.OPEN_LOCK:
        v1.OPEN[unit] = u
    try:
        u.start(v1.job_of(dir))
    except BaseException:
        with v1.OPEN_LOCK:
            v1.OPEN.pop(unit, None)
        u.close()
        raise
    return {"prompt": prompt(unit, u.expected),
            "mcp": [{"name": "computer", "server": "computer", "args": [u.socket()]}], "agent_seconds": v1.AGENT_SECONDS}


def task_given(unit, dir, **_):
    """The agent is being given its task: the window before it closes, and ``late``'s count starts."""
    with v1.OPEN_LOCK:
        u = v1.OPEN.get(unit)
    if u is None:
        raise ValueError(f"{unit} was not set up")
    u.task_given()
    return {}


def score(unit, dir, transcript, answer, **_):
    with v1.OPEN_LOCK:
        u = v1.OPEN.pop(unit, None)
    if u is None:
        raise ValueError(f"{unit} was not set up")
    try:
        u.end()
        events = u.events()
        actions = list(u.desktop.actions)
        calls = u.calls()
        try:
            u.keep(events)
        except OSError:
            pass
    finally:
        u.remove()
    message = "\n".join(x for x in (answer or "", u.final) if x)
    if unit == "late":
        v = late_verdict(u, events, actions, message)
    else:
        v = checks.verdict(unit, u.expected, events, actions, message, calls, u.bridge.past)
    passed = v["passed"] and u.error is None
    detail = {"said": (answer or "")[:200], "steps": u.steps, "steps_used": u.bridge.used, "past_budget": u.bridge.past,
              "calls": [c.get("tool") for c in calls], "errors": sum(1 for c in calls if c.get("isError")),
              "actions": len(actions), "events": sum(1 for e in events if e.get("kind") in checks.POINTER + checks.KEYS)}
    why = v["why"] or u.error
    if why:
        detail["why"] = why
    return {"reward": 1.0 if passed else 0.0, "passed": passed, "detail": detail}


def oracle(unit, dir, **_):
    """The reference: given its task at once, then version 1's calls; for ``late``, steps that look again until the
    button is up and an observation shows it, then its click."""
    with v1.OPEN_LOCK:
        u = v1.OPEN.get(unit)
    if u is None:
        raise ValueError(f"{unit} was not set up")
    u.task_given()
    if unit != "late":
        return v1.oracle(unit, dir)
    c = v1.Client(u.socket())
    try:
        target = u.expected["target"]
        for _ in range(u.steps - 1):
            failed, _ = c.call("wait")
            failed_shot, png = c.call("screenshot")
            if failed or failed_shot or not isinstance(png, bytes):
                raise v1.ReferenceFailed("a look by a step did not answer as a correct agent's would")
            if u.up.is_set() and u.pictures[-1][0] > (u.shown or float("inf")):
                break                                # this step's observation was taken with the button up
        else:
            raise v1.ReferenceFailed("the button was not seen within the steps")
        failed, said = c.call("click", dict(zip("xy", v1.centre(u.boxes[target]))))
        if failed:
            raise v1.ReferenceFailed(f"click answered with an error: {str(said)[:200]}")
    finally:
        c.close()
    return {"answer": "done"}


def main():
    atexit.register(v1.REAPER.close)
    atexit.register(v1.close_all)                 # runs first: the units go, then the reaper is let go with nothing
    serve({"units": units, "setup": setup, "task_given": task_given, "score": score, "oracle": oracle,
              "end": v1.end})


if __name__ == "__main__":
    main()
