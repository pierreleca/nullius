"""Task sets: a named list of a benchmark's units, kept in the user catalogue.

An entry is ``sets/<benchmark>/<name>.json`` under the user catalogue
(``paths.catalogue_dir``, ``NULLIUS_CATALOGUE``): a plain file to copy or keep
in git. A set is a group of tasks, run as a benchmark's own dataset is: it
holds an id minted when it is made, its name and description, the benchmark,
the dataset it is cut from, and its revisions, and nothing that binds a run
(no benchmark option, no setup). A revision is one membership: its number,
its date, a note, the unit ids and a content hash over the benchmark, the
dataset and the sorted ids. Two runs ran the same set when their jobs name the
same id; the revision and the hash say which membership. Two entries with
equal lists stay two sets. An entry holding any other field is refused where
it is read.

Only a change of membership adds a revision; the earlier ones stay in the
file, and ``NAME@REV`` names one. ``record`` is what a job's ``config.json``
keeps of the set it ran (``standard.task_set``): its id, name, revision, hash
and units. With the job's benchmark and dataset that is enough for
``import_path`` to rebuild the entry on another machine, its description
empty and the revision dated by the import. The same id and revision with
another hash is refused there.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from nullius import paths
from nullius.runs import classify
from nullius.standard import definitions

SCHEMA = "nullius.task-set/v1"
FOLDER = "sets"
# A name stays inside its benchmark's folder: no slash, no leading dot, no ``..``
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TAG = re.compile(r"__u(\d+)(?:r\d+)?$")
# The outcome groups ``--outcome`` takes beside the pages' outcome names: a pass, a verdict against the
# agent (its failures and its own errors, as the attempts' pass count reads them), the infrastructure's
GROUPS = {
    "passed": {classify.RESOLVED},
    "failed": {classify.PATCH_FAILED_TESTS, classify.FAILED_VERIFICATION, classify.PATCH_NOT_APPLIED,
               classify.NO_PATCH, classify.AGENT_ERROR},
    "infra": set(classify.INFRA_OUTCOMES),
}
HEADER = ("id", "name", "description", "benchmark", "dataset")
REVISION = ("revision", "date", "note", "units", "hash")
RECORD = ("id", "name", "revision", "hash", "units")


class Refused(Exception):
    """The set cannot be made, changed, read or run as asked; the message says why."""


class NotFound(Refused):
    """No such set, revision, file or job."""


def root_dir(root: Optional[str] = None) -> str:
    return root if root is not None else paths.catalogue_dir()


def check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
        raise Refused(f"{name!r} is not a task set's name: letters, digits, '.', '_' and '-', "
                      "starting with a letter or a digit, at most 64, no '..'")
    return name


def _bench_key(benchmark: str) -> str:
    if not isinstance(benchmark, str) or not definitions._KEY.match(benchmark):
        raise Refused(f"{benchmark!r} is not a benchmark's key")
    return benchmark


def _contained(path: str, folder: str) -> str:
    resolved = os.path.realpath(path)
    if os.path.commonpath((resolved, folder)) != folder:
        raise Refused(f"{path}: outside the task set folder {folder}")
    return resolved


def _folder(benchmark: Optional[str], root: Optional[str]) -> str:
    folder = os.path.join(os.path.realpath(root_dir(root)), FOLDER)
    if benchmark is not None:
        folder = os.path.join(folder, _bench_key(benchmark))
    return _contained(folder, folder)


def path_of(benchmark: str, name: str, root: Optional[str] = None) -> str:
    folder = _folder(benchmark, root)
    return _contained(os.path.join(folder, check_name(name) + ".json"), folder)


def split_ref(ref: str) -> Tuple[str, Optional[int]]:
    """``NAME`` or ``NAME@REV``: (name, revision or None)."""
    name, at, rev = str(ref).partition("@")
    if not at:
        return check_name(name), None
    if not rev.isdigit() or int(rev) < 1:
        raise Refused(f"{ref!r}: NAME@REV, REV a revision's number (1, 2, ...)")
    return check_name(name), int(rev)


def content_hash(benchmark: str, dataset: str, units: Iterable[str]) -> str:
    """The sha256 over what makes the membership: the benchmark, the dataset and the ids sorted (an order, a
    comment, a file's name say nothing)."""
    doc = {"benchmark": benchmark, "dataset": dataset, "units": sorted(set(str(u) for u in units))}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _hash_of(entry: dict, units: Iterable[str]) -> str:
    return content_hash(entry["benchmark"], entry["dataset"], units)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ids(units: Iterable[str], what: str) -> List[str]:
    ids = [str(u) for u in units]
    if any(not u or u.startswith("#") or u != u.strip() for u in ids):
        raise Refused(f"{what}: an id is a word with no space around it, not empty and not starting with #")
    twice = sorted({u for u in ids if ids.count(u) > 1})
    if twice:
        raise Refused(f"{what} lists {', '.join(twice)} more than once")
    if not ids:
        raise Refused(f"{what} lists no unit")
    return ids


def check(entry: dict, where: str) -> dict:
    """``entry`` as read from ``where``, its shape and every revision's hash checked."""
    if not isinstance(entry, dict) or entry.get("schema") != SCHEMA:
        raise Refused(f"{where}: not a task set (schema {SCHEMA})")
    other = sorted(set(entry) - {"schema", *HEADER, "revisions"})
    if other:
        raise Refused(f"{where}: holds {', '.join(other)}, which a task set does not hold: a set is its dataset, "
                      "its ids and its revisions, and a run gives its own options")
    for k in ("id", "name", "benchmark", "dataset"):
        if not isinstance(entry.get(k), str) or not entry[k]:
            raise Refused(f"{where}: no {k}")
    check_name(entry["name"])
    _bench_key(entry["benchmark"])
    if not isinstance(entry.get("description"), str):
        raise Refused(f"{where}: description is text")
    revs = entry.get("revisions")
    if not isinstance(revs, list) or not revs:
        raise Refused(f"{where}: no revision")
    seen = set()
    for r in revs:
        n = r.get("revision") if isinstance(r, dict) else None
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n in seen:
            raise Refused(f"{where}: a revision's number is a whole number, each once")
        seen.add(n)
        if not isinstance(r.get("date"), str) or not r["date"]:
            raise Refused(f"{where}: revision {n} has no date")
        if not isinstance(r.get("note"), str):
            raise Refused(f"{where}: revision {n}'s note is text")
        if not isinstance(r.get("units"), list) or any(not isinstance(u, str) for u in r["units"]):
            raise Refused(f"{where}: revision {n} lists no units")
        _ids(r["units"], f"{where}: revision {n}")
        if r.get("hash") != _hash_of(entry, r["units"]):
            raise Refused(f"{where}: revision {n}'s hash is not that of its units and dataset: "
                          "the file was changed by hand")
    return entry


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise NotFound(f"{path}: no such file")
    except (OSError, ValueError) as e:
        raise Refused(f"{path}: cannot read it as JSON: {e}")


def load(benchmark: str, name: str, root: Optional[str] = None) -> dict:
    path = path_of(benchmark, name, root)
    if not os.path.isfile(path):
        raise NotFound(f"no task set {name!r} of {benchmark} (in {os.path.dirname(path)})")
    entry = check(_read(path), path)
    if entry["name"] != name or entry["benchmark"] != benchmark:
        raise Refused(f"{path}: it names {entry['benchmark']}/{entry['name']}, not {benchmark}/{name}")
    return entry


def revision(entry: dict, rev: Optional[int] = None) -> dict:
    """Revision ``rev`` of ``entry``, its latest when None."""
    revs = sorted(entry["revisions"], key=lambda r: r["revision"])
    if rev is None:
        return revs[-1]
    got = next((r for r in revs if r["revision"] == rev), None)
    if got is None:
        raise NotFound(f"{entry['name']} has no revision {rev}: its revisions are {', '.join(str(r['revision']) for r in revs)}")
    return got


def entries(benchmark: Optional[str] = None, root: Optional[str] = None) -> Tuple[List[dict], List[str]]:
    """Every entry (of ``benchmark``), by benchmark and name; and the files that could not be read."""
    base = _folder(None, root)
    got, bad = [], []
    try:
        benches = sorted(os.listdir(base)) if benchmark is None else [_bench_key(benchmark)]
    except FileNotFoundError:
        return [], []
    for b in benches:
        if not definitions._KEY.match(b):
            continue
        try:
            folder = _folder(b, root)
        except Refused as e:
            bad.append(str(e))
            continue
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".json") or fname.startswith("."):
                continue
            try:
                got.append(load(b, fname[:-5], root))
            except Refused as e:
                bad.append(str(e))
    return got, bad


def write(entry: dict, root: Optional[str] = None) -> str:
    """The entry written whole, in place of the old file at once."""
    path = path_of(entry["benchmark"], entry["name"], root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = {"schema": SCHEMA, **{k: entry[k] for k in HEADER if k in entry},
               "revisions": sorted(entry["revisions"], key=lambda r: r["revision"])}
    fd, tmp = tempfile.mkstemp(prefix=".set-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ordered, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def _dataset(bench: dict, dataset: Optional[str]) -> str:
    ids = [d["id"] for d in bench.get("datasets") or []]
    if not dataset:
        raise Refused(f"--dataset: the dataset the set is cut from, one of {', '.join(ids)}")
    if dataset not in ids:
        raise Refused(f"{bench['label']} has no dataset {dataset!r}: {', '.join(ids)}")
    return dataset


def create(bench: dict, name: str, *, dataset: str, units: List[str], description: str = "", note: str = "",
           root: Optional[str] = None) -> dict:
    """A new entry at revision 1, written; refused when one of that name exists."""
    check_name(name)
    path = path_of(bench["key"], name, root)
    if os.path.exists(path):
        raise Refused(f"a task set {name!r} of {bench['key']} exists already ({path}): set update changes it")
    entry = {"id": uuid.uuid4().hex, "name": name, "description": description or "", "benchmark": bench["key"],
             "dataset": _dataset(bench, dataset)}
    ids = _ids(units, f"the set {name}")
    entry["revisions"] = [{"revision": 1, "date": _now(), "note": note or "", "units": ids, "hash": _hash_of(entry, ids)}]
    write(entry, root)
    return entry


def update(benchmark: str, name: str, *, add: Optional[List[str]] = None, remove: Optional[List[str]] = None,
           units: Optional[List[str]] = None, note: str = "", root: Optional[str] = None) -> Tuple[dict, bool]:
    """(the entry, whether a revision was added): a change of membership adds one, kept beside the earlier
    ones; the same membership adds none. Removing an id the set does not hold is refused, as is a set left
    with no unit."""
    entry = load(benchmark, name, root)
    last = revision(entry)
    now = list(last["units"])
    if units is not None:
        new = _ids(units, "the list")
    elif add:
        new = now + [u for u in dict.fromkeys(_ids(add, "--add")) if u not in now]
    elif remove:
        gone = _ids(remove, "--remove")
        absent = [u for u in gone if u not in now]
        if absent:
            raise Refused(f"{name} does not hold {', '.join(absent)}")
        new = [u for u in now if u not in gone]
        if not new:
            raise Refused(f"{name} would hold no unit")
    else:
        raise Refused("set update: --add, --remove or --units")
    if set(new) == set(now):
        return entry, False
    entry["revisions"].append({"revision": last["revision"] + 1, "date": _now(), "note": note or "", "units": new,
                               "hash": _hash_of(entry, new)})
    write(entry, root)
    return entry, True


def _job_config(job_dir: str) -> dict:
    cfg_path = os.path.join(job_dir, "config.json")
    if not os.path.isfile(cfg_path):
        raise NotFound(f"{job_dir}: no job (no config.json)")
    cfg = _read(cfg_path)
    if cfg.get("runner") != "standard" or not isinstance(cfg.get("standard"), dict):
        raise Refused(f"{job_dir}: not a job of the Nullius standard")
    return cfg


def _place(trial: str) -> int:
    m = _TAG.search(trial)
    return int(m.group(1)) if m else 1 << 30


def outcome_names(given: Optional[List[str]]) -> set:
    """``--outcome O,...`` (repeatable): the pages' outcome names, or passed, failed, infra."""
    out = set()
    for word in (w.strip() for g in given or [] for w in g.split(",")):
        if not word:
            continue
        if word in GROUPS:
            out |= GROUPS[word]
        elif word in classify.ORDER:
            out.add(word)
        else:
            raise Refused(f"--outcome {word}: one of {', '.join(classify.ORDER)}, or {', '.join(GROUPS)}")
    return out


def _job_dataset(cfg: dict) -> str:
    return str(((cfg.get("datasets") or [{}])[0].get("name") or "")).rpartition("/")[2]


def from_job(bench: dict, job_dir: str, outcomes: Optional[List[str]] = None, named: Optional[List[str]] = None) -> dict:
    """What a set made from a job holds: {dataset, units, note}. The trials of the
    ``outcomes`` and the units ``named`` (each the full id a trial's ``standard.unit`` records), every unit
    the job ran when neither is given; a unit of several attempts is taken when any of them is."""
    from nullius.runs import parse
    from nullius.standard import job
    job_dir = os.path.abspath(job_dir.rstrip("/"))
    cfg = _job_config(job_dir)
    std = cfg["standard"]
    if std.get("benchmark") != bench["key"]:
        raise Refused(f"{job_dir} ran {std.get('benchmark')}, not {bench['key']}")
    dataset = _job_dataset(cfg)
    wanted = outcome_names(outcomes)
    trials = []
    for trial_dir in job.trial_dirs(job_dir):
        res = _read(os.path.join(trial_dir, "result.json"))
        unit = (res.get("standard") or {}).get("unit")
        if unit is None:
            continue
        t = parse.parse_trial(os.path.basename(job_dir), trial_dir)
        trials.append((_place(os.path.basename(trial_dir)), str(unit), t.outcome if t else classify.UNKNOWN))
    trials.sort()
    held = list(dict.fromkeys(u for _, u, _ in trials))
    if not held:
        raise Refused(f"{job_dir}: no trial records its unit")
    names = list(dict.fromkeys(named or []))
    unknown = [u for u in names if u not in held]
    if unknown:
        raise Refused(f"{job_dir} has no trial of {', '.join(unknown)}")
    if wanted or names:
        chosen = {u for _, u, o in trials if o in wanted} | set(names)
        units = [u for u in held if u in chosen]
    else:
        units = held
    if not units:
        raise Refused(f"{job_dir} has no trial of the outcomes {', '.join(sorted(wanted))}")
    said = ([f"outcomes {','.join(outcomes)}"] if outcomes else []) + ([f"{len(names)} named"] if names else [])
    return {"dataset": dataset, "units": units, "note": f"from the job {cfg.get('job_name') or os.path.basename(job_dir)}"
            + (f": {'; '.join(said)}" if said else "")}


def record(entry: dict, rev: dict) -> dict:
    """What a job's config keeps of the set it ran (``standard.task_set``): its id, name, revision, hash and units."""
    return {"id": entry["id"], "name": entry["name"], "revision": rev["revision"], "hash": rev["hash"], "units": rev["units"]}


def for_run(bench: dict, ref: str, *, dataset: Optional[str] = None, units_given: bool = False,
            root: Optional[str] = None) -> Tuple[dict, str]:
    """``run --set NAME[@REV]``: (the record the job keeps, the dataset), the set's dataset taken over the run's.
    A ``--units`` beside it, or another ``--dataset``, is refused. The run's benchmark options are its own, as
    on the whole dataset: the run then refuses an id its driver does not list under them."""
    name, rev = split_ref(ref)
    if units_given:
        raise Refused(f"--set {ref} and --units: the set lists the units, give one or the other")
    entry = load(bench["key"], name, root)
    got = revision(entry, rev)
    if dataset and dataset != entry["dataset"]:
        raise Refused(f"--dataset {dataset}: the task set {name} is cut from the dataset {entry['dataset']}")
    return record(entry, got), entry["dataset"]


def import_path(path: str, root: Optional[str] = None) -> Tuple[dict, List[int]]:
    """(the entry, the revisions added): an entry's file, or a job that ran a set (its ``standard.task_set``),
    merged into the entry of that name here. Refused when the entry here has another id, when another entry
    holds the id, and when a revision here of the same number has another hash."""
    path = os.path.abspath(path.rstrip("/"))
    if os.path.isdir(path):
        cfg = _job_config(path)
        rec = cfg["standard"].get("task_set")
        if not isinstance(rec, dict):
            raise NotFound(f"{path}: the job ran no task set")
        missing = [k for k in RECORD if k not in rec]
        if missing:
            raise Refused(f"{path}: its task set's record has no {', '.join(missing)}")
        incoming = {"schema": SCHEMA, "id": rec["id"], "name": rec["name"], "description": "",
                    "benchmark": cfg["standard"].get("benchmark"), "dataset": _job_dataset(cfg),
                    "revisions": [{"revision": rec["revision"], "date": _now(),
                                   "note": f"from the job {cfg.get('job_name') or os.path.basename(path)}",
                                   "units": rec["units"], "hash": rec["hash"]}]}
    elif os.path.isfile(path):
        incoming = _read(path)
    else:
        raise NotFound(f"{path}: no such file or job")
    check(incoming, path)
    bench, name = incoming["benchmark"], incoming["name"]
    others, _ = entries(bench, root)
    holder = next((e for e in others if e["id"] == incoming["id"] and e["name"] != name), None)
    if holder:
        raise Refused(f"the set {incoming['id']} is here already, named {holder['name']}")
    target = path_of(bench, name, root)
    if not os.path.exists(target):
        incoming = {k: v for k, v in incoming.items() if k != "schema"}
        write(incoming, root)
        return incoming, sorted(r["revision"] for r in incoming["revisions"])
    here = load(bench, name, root)
    if here["id"] != incoming["id"]:
        raise Refused(f"a task set {name!r} of {bench} with another id is here already ({target})")
    if here["dataset"] != incoming["dataset"]:
        raise Refused(f"the set {name} here differs in its dataset: {here['dataset']!r}, not {incoming['dataset']!r}")
    have = {r["revision"]: r for r in here["revisions"]}
    added = []
    for r in incoming["revisions"]:
        old = have.get(r["revision"])
        if old is None:
            here["revisions"].append(r)
            added.append(r["revision"])
        elif old["hash"] != r["hash"]:
            raise Refused(f"revision {r['revision']} of {name} holds another list here (hash {old['hash'][:12]}, "
                          f"not {r['hash'][:12]}): the same id and revision with another hash")
    if added:
        write(here, root)
    return here, sorted(added)
