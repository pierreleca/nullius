"""Chosen tasks of a finished job, run again in that job.

The user names trials of a job that has ended - a trial by its name, or a
unit by its id for each of its attempts - and each runs again where it is, by
the path an automatic retry takes (``run.Run.try_again``): the trial's folder
moves into the job's ``retried/<trial>.try-N``, kept whole with its calls and
its cost, and the unit runs again under the trial's name, with its place in
the dataset and its attempt. A try that is stopped puts the one before it
back. Every task named runs again, whatever its result was; the new result
takes its place, and the tries before it stay readable beside it.

The settings are the job's own, read from its ``config.json`` and never
given again; the endpoint and the key come from the environment, as for a
run. The benchmark and the agent must resolve to the versions the job ran.
Refused before anything moves: no task named, an id the job does not hold, a
job still running (no ``result.json`` yet, or a re-run of it under way), a
merge of shards that differed (``merge --force``: a trial's settings may not
be the config's), and a benchmark or an agent that resolves to another
version now. A merged job is not refused for its hard links: a re-run renames
a trial's folder and writes the new try into a new one, and no linked file is
written.

What is recorded: the try asked for gets ``standard.rerun`` (by hand, when,
the reason, the result it replaced) and ``standard.retries``, the tries
before it, as an automatic retry's does. The job's ``result.json`` is
recomputed from its trials as a merged job's is (``merge.result_of``); its
start, its end and its ``budget`` stay the first run's, and ``reruns`` lists
each re-run: when, the reason, the trials, the results replaced, its cap and
its spend. The live feed gets the re-run's ``job.start``, its trials' events
and its ``job.end``, numbered on from the file's last line.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from typing import Callable, Dict, List, Optional

from nullius.harbor import feed as live_feed
from nullius.standard import boat, definitions, job, ledger as ledgers, merge, run

LOCK = "rerun.lock"                          # in the job's .work: held while a re-run of it runs
_TAG = re.compile(r"__u(\d{4,})(?:r(\d+))?$")


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def said(res: dict) -> dict:
    """What a try ended with, as a re-run's record keeps it: its error's kind, or its reward or score."""
    exc = res.get("exception_info") or {}
    if exc.get("exception_type"):
        return {"error": exc["exception_type"]}
    return dict((res.get("verifier_result") or {}).get("rewards") or {})


def _cost(res: dict) -> Optional[float]:
    c = (res.get("agent_result") or {}).get("cost_usd")
    return c if _number(c) else None


def running(job_dir: str) -> bool:
    """Whether a re-run of the job is under way: another process holds its lock."""
    path = os.path.join(job_dir, ".work", LOCK)
    if not os.path.isfile(path):
        return False
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def _hold(job_dir: str):
    """The job's lock, held until the file is closed; refused when another re-run holds it."""
    os.makedirs(os.path.join(job_dir, ".work"), exist_ok=True)
    f = open(os.path.join(job_dir, ".work", LOCK), "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise run.Refused(f"{os.path.basename(job_dir)} is being run again already: wait for that re-run to end")
    return f


def _last_seq(path: str) -> int:
    """The last ``seq`` of a feed, which the re-run's lines go on from: its board folds them in that order."""
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    seq = json.loads(line).get("seq")
                except (ValueError, AttributeError):
                    continue
                if isinstance(seq, int) and not isinstance(seq, bool):
                    n = max(n, seq)
    except OSError:
        pass
    return n


def resolve(cfg: dict, results: List[dict], root: Optional[str] = None) -> tuple:
    """(benchmark, agent or other) a job ran, from today's catalogue; refused, naming the difference, when either
    is gone or resolves to another version than the job recorded (its config, its trials' ``agent_info``)."""
    std = cfg["standard"]
    ref = std.get("benchmark_ref") or std.get("benchmark") or ""
    bench = definitions.get("benchmark", ref, root) or next((k for k in definitions.kits() if k["key"] == ref), None)
    if bench is None:
        raise run.Refused(f"the job ran the benchmark {ref}, which the catalogue no longer holds")
    ran = {k: std[k] for k in ("benchmark_version", "benchmark_ref") if std.get(k)}
    if job._version(bench) != ran:
        now = job._version(bench).get("benchmark_version") or "no version"
        raise run.Refused(f"the job ran {ref} {ran.get('benchmark_version') or 'with no version'}, and it resolves to "
                          f"{now} now: start a new run to compare")
    kind, plugged = std.get("kind") or "agent", str(std.get("plugged") or "")
    if kind == "control":
        other = definitions.control(bench, plugged)
    elif kind == "upstream":
        other, _ = definitions.setup(bench, plugged, str((std.get("setup") or {}).get("asked") or ""), std.get("provider") or "")
    else:
        other = definitions.get(kind, plugged, root)
    if other is None:
        raise run.Refused(f"the job ran {plugged}, which the catalogue no longer holds")
    recorded = sorted({str((r.get("agent_info") or {}).get("version") or "") for r in results})
    now = str((other.get("upstream") or {}).get("version") or "")
    if recorded and recorded != [now]:
        raise run.Refused(f"the job ran {plugged} {', '.join(v or 'with no version' for v in recorded)}, and {plugged} "
                          f"resolves to {now or 'no version'} now: start a new run to compare")
    return bench, other


def chosen(job_dir: str, ids: List[str], trials: Dict[str, dict]) -> List[str]:
    """The trials ``ids`` name, in the order named, each once: an id is a trial's name, or a unit's id for each
    of its attempts. Refused for no id, or for ids the job does not hold."""
    if not ids:
        raise run.Refused("no task named: --unit ID (repeatable) or --units FILE")
    by_unit: Dict[str, List[str]] = {}
    for name, res in trials.items():
        by_unit.setdefault(str((res.get("standard") or {}).get("unit") or name), []).append(name)
    unknown = [i for i in ids if i not in trials and i not in by_unit]
    if unknown:
        raise run.Refused(f"{os.path.basename(job_dir)} holds no trial or unit {', '.join(unknown)}")
    return list(dict.fromkeys(n for i in ids for n in ([i] if i in trials else by_unit[i])))


def rerun(job_dir: str, ids: List[str], *, reason: str = "", concurrency: Optional[int] = None, retries: int = 0,
          max_usd: Optional[float] = None, env=os.environ, say: Callable[[str], None] = print, licences=None,
          prices_unreachable: Optional[str] = None, root: Optional[str] = None) -> dict:
    """Run the trials ``ids`` name (:func:`chosen`) of the ended job ``job_dir`` again, in that job. ``retries``:
    automatic retries of each re-run try, as a run's; ``max_usd``: the re-run's own spending cap; ``concurrency``:
    the job's unless given. ``root``: a catalogue folder to resolve the definitions in (the user's and the
    bundled ones by default). Returns the record added to the job's ``reruns``; ``run.Refused`` before anything
    moves."""
    job_dir = os.path.abspath(job_dir.rstrip("/"))
    cfg, old = merge._read(os.path.join(job_dir, "config.json")), merge._read(os.path.join(job_dir, "result.json"))
    if cfg.get("runner") != "standard" or not isinstance(cfg.get("standard"), dict):
        raise run.Refused(f"{job_dir}: not a job of the Nullius standard (no config.json naming it)")
    if not old or running(job_dir):
        raise run.Refused(f"{os.path.basename(job_dir)} is still running: a job is run again once it has ended")
    std = cfg["standard"]
    if std.get("merged_despite"):
        raise run.Refused(f"{os.path.basename(job_dir)} merges shards that differed ({'; '.join(std['merged_despite'])}): "
                          "a trial's settings may not be its config's. Run it again in its shard's job")
    names = merge._trials(job_dir)
    trials = {n: merge._read(os.path.join(job_dir, n, "result.json")) for n in names}
    picked = chosen(job_dir, ids, trials)
    bench, other = resolve(cfg, list(trials.values()), root)
    model = str(((cfg.get("agents") or [{}])[0] or {}).get("model_name") or "")
    dataset = str(((cfg.get("datasets") or [{}])[0] or {}).get("name") or "").partition("/")[2]
    prices = ((std.get("prices") or {}).get("models") or {})
    r = run.Run(bench, other, model=str((std.get("setup") or {}).get("asked") or model), provider=std.get("provider") or "openai",
                dataset=dataset or None, concurrency=concurrency or cfg.get("n_concurrent_trials") or 4,
                jobs_root=os.path.dirname(job_dir), name=os.path.basename(job_dir), say=say, env=env,
                unsandboxed=bool(std.get("unsandboxed")), judge_model=std.get("judge") or None,
                backend=std.get("backend") or "local", boat_size=std.get("boat_size") or boat.SIZE,
                options=std.get("options") or None, bench_options=std.get("benchmark_options") or None,
                repeat=int(std.get("repeat") or 1), retries=retries, simulator_model=std.get("simulator") or None,
                licences=licences, prices={m: {k: v for k, v in row.items() if k != "source"} for m, row in prices.items()},
                max_usd=max_usd, max_usd_per_unit=(old.get("budget") or {}).get("unit_max_usd"),
                prices_unreachable=prices_unreachable, constraints=std.get("constraints") or None,
                timeouts=std.get("timeouts") or None, max_turns=std.get("max_turns"), max_calls=std.get("max_calls"),
                served_by=std.get("served_by") or None)
    # each trial's unit, its place and its attempt, as the runner named it; the try it is run again as
    work, first_try = [], {}
    for name in picked:
        unit, tag = str((trials[name].get("standard") or {}).get("unit") or ""), _TAG.search(name)
        i, a = (int(tag.group(1)), int(tag.group(2) or 1)) if tag else (-1, 1)
        if not tag or r.trial_name(i, unit, a) != name:
            raise run.Refused(f"{name} is not a trial this runner named: it cannot be run again in place")
        first_try[name] = int((trials[name].get("standard") or {}).get("retries") or 0) + 1
        if os.path.lexists(os.path.join(job_dir, "retried", f"{name}.try-{first_try[name]}")):
            raise run.Refused(f"retried/{name}.try-{first_try[name]} is there already, and {name} does not count it: "
                              "the job's tries do not add up")
        work.append((i, unit, a))
    held = _hold(job_dir)
    try:
        return _again(r, job_dir, old, cfg, trials, picked, work, first_try, reason, t0=time.time())
    finally:
        held.close()


def _again(r: "run.Run", job_dir: str, old: dict, cfg: dict, trials: Dict[str, dict], picked: List[str], work: list,
           first_try: Dict[str, int], reason: str, t0: float) -> dict:
    """The re-run itself, the job held (:func:`rerun`)."""
    r._tries = dict(first_try)
    n = len(old.get("reruns") or []) + 1
    if (r.max_usd is not None or r.unit_max_usd is not None) and r.base:
        # the re-run's own cap, in a ledger of its own: the first run's stays as it was
        r.ledger = ledgers.Ledger.create(os.path.join(job_dir, ".work", f"rerun-{n}", "ledger", "ledger.json"), r.max_usd, r.unit_max_usd)
    p = r.progress
    if r.cap_dropped:
        r.say(f"warning: {r.cap_dropped} ({r.cap_why})")
    r.known_models()
    r.ready()                                # refused here, the job reads as it did: nothing has moved
    p.emit("plan", stages="units")           # a further part of the job: its units said after the first's
    r.n_units = len(work)
    p.emit("units", "start", n=len(work), of=len(trials), noun=r.bench["units"]["nouns"])
    path = os.path.join(job_dir, live_feed.FEED)
    r.feed = live_feed.Writer(path, r.name)
    r.feed.seq = _last_seq(path)
    r.feed.emit("job.start", n_total=int(old.get("n_total_trials") or len(trials)), n_concurrent=r.concurrency,
                agents=[r.other["key"]], tasks=[u for _, u, _ in work],
                **({"budget": {k: v for k, v in (("max_usd", r.max_usd), ("unit_max_usd", r.unit_max_usd)) if v is not None}}
                   if r.ledger is not None else {}))
    mark = {"by": "hand", "at": job.iso(t0), **({"reason": reason} if reason else {})}

    def first(iur, work_unit):
        name = r.trial_name(*iur)
        res = r.try_again(name, iur, first_try[name], work_unit)
        if res is not None:                  # the try asked for: its record says so, before a retry may move it
            res["standard"] = dict(res.get("standard") or {}, rerun=dict(mark, replaced=said(trials[name])))
            job._write(os.path.join(job_dir, name, "result.json"), json.dumps(res, indent=1))
        return res

    record: dict = {}

    def write(ended: List[dict], budget: Optional[dict]) -> None:
        now = {name: merge._read(os.path.join(job_dir, name, "result.json")) for name in merge._trials(job_dir)}
        every = list(now.values())
        out = dict(old, **{k: v for k, v in merge.result_of([{"result": old, "trials": list(now)}], every).items() if k != "budget"})
        if r.repeat > 1:
            hat = job.pass_hat(every)
            out.pop("pass_hat_k", None)
            out.update({"pass_hat_k": hat} if hat else {})
        if not (cfg["standard"].get("merged_from")):  # a merged job's is its shards': this job has no relay of its own
            who = job.served_total(every, job.served(r.benchmark_calls(), getattr(r, "serving", None)))
            out.pop("served", None)
            out.update({"served": who} if who else {})
        replaced, costs = [], []
        for name in picked:
            res, k = now.get(name) or {}, first_try[name]
            last = int((res.get("standard") or {}).get("retries") or 0)
            if last < k:
                continue                     # not begun, or stopped: the try before is back
            replaced.append({"trial": name, "unit": str((res.get("standard") or {}).get("unit") or ""), "try": k,
                             "was": said(trials[name]), "now": said(res)})
            costs += [_cost(res)] + [_cost(merge._read(os.path.join(job_dir, "retried", f"{name}.try-{t}", "result.json")))
                                     for t in range(k + 1, last + 1)]
        known = [c for c in costs if c is not None]
        spent = budget["spent_usd"] if budget else (round(sum(known), 6) if known else None)
        record.update({"at": job.iso(t0), "finished_at": job.iso(time.time()), **({"reason": reason} if reason else {}),
                       "trials": list(picked), "replaced": replaced, "max_usd": r.max_usd, "spent_usd": spent,
                       **({"stopped": True} if r._stopped.is_set() or (budget or {}).get("stopped") else {})})
        out["reruns"] = list(old.get("reruns") or []) + [dict(record)]
        job._write(os.path.join(job_dir, "result.json"), json.dumps(out, indent=1))

    ended, budget, u0 = r.run_units(work, write, first)
    p.emit("units", "done", seconds=round(time.time() - u0, 1))
    p.emit("done", n=len(ended), seconds=round(time.time() - t0, 1))
    if budget and budget.get("max_usd") is not None:
        r.say(f"spent {ledgers.usd(budget['spent_usd'] or 0)} of the {ledgers.usd(budget['max_usd'])} USD cap"
              + (": stopped there" if budget.get("stopped") else ""))
    r.say(f"ran again: {len(record['replaced'])} of {len(picked)} replaced"
          + "".join(f"; {x['trial']}: {_word(x['was'])} -> {_word(x['now'])}" for x in record["replaced"]))
    return dict(record, job=job_dir)


def _word(result: dict) -> str:
    """A try's result in a line: ``error KIND``, ``reward 1.0``, ``score 0.5``."""
    if "error" in result:
        return f"error {result['error']}"
    return " ".join(f"{k} {v}" for k, v in result.items()) or "no verdict"
