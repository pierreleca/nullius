"""A benchmark's tasks listed with what each is about, searched and counted by category: what
``nullius standard units`` prints and what a page asks for, a page at a time (``find``).

The driver is started as ``prove`` starts it, with the options given and the others at their defaults, after
the same preparation as a run's: the upstream at its tested commit, the dataset's file checked by its
sha256, the driver's own environment, each made once when it is not there. It is given no licence and no
key, and it starts no unit. Its ``units`` gives the ids; when its definition says ``driver.describe``, its
``describe`` gives each a title, a text and named categories, read from the dataset's files alone. A driver
without it gives ids, and a search reads the ids.

The listing is kept in the user cache (``paths.cache_dir()``, ``standard-units/<benchmark>/``) under a digest
of what makes it: the benchmark's definition, the files its driver is started with, the dataset and the
option values.
The same listing again reads that file and starts nothing.

A search is words: a task matches when each word is in its id, its title or its text, whatever the case. A
category filter is FIELD=VALUE: values of one field widen the match (either), fields narrow it (both). Each
field's values are counted over the tasks the search and the other fields' filters match, so a value chosen
leaves its field's other values counted.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from typing import Dict, List, Optional

from nullius import paths
from nullius.standard import definitions, install, jsonl, run

PAGE = 100                       # tasks a page
FOLDER = "standard-units"
SECONDS = 300                    # a driver's time for each of its two answers


class Refused(Exception):
    """The listing cannot be made as asked; the message says why."""


class NotFound(Refused):
    """No such benchmark or dataset."""


def dataset_of(bench: dict, dataset: Optional[str]) -> dict:
    ids = [d["id"] for d in bench.get("datasets") or []]
    if not dataset:
        raise Refused(f"--dataset: one of {', '.join(ids)}")
    got = next((d for d in bench.get("datasets") or [] if d["id"] == dataset), None)
    if got is None:
        raise NotFound(f"{bench.get('label') or bench['key']} has no dataset {dataset!r}: {', '.join(ids)}")
    return got


def option_values(bench: dict, dataset: str, given: Optional[dict] = None) -> Dict[str, str]:
    """The options the driver lists under: those given, the others at their defaults."""
    values, bad = definitions.bench_options_of(bench, dataset, given)
    if bad:
        raise Refused("; ".join(bad))
    return {str(k): str(v) for k, v in sorted(values.items())}


def categories_of(given: Optional[List[str]]) -> Dict[str, List[str]]:
    """``--category FIELD=VALUE`` (repeatable): the values asked of each field, in the order given."""
    out: Dict[str, List[str]] = {}
    for word in given or []:
        field, eq, value = str(word).partition("=")
        if not eq or not field.strip() or not value.strip():
            raise Refused(f"--category {word}: FIELD=VALUE")
        values = out.setdefault(field.strip(), [])
        if value.strip() not in values:
            values.append(value.strip())
    return out


def filter_text(search: str = "", categories: Optional[Dict[str, List[str]]] = None) -> str:
    """The filter as the command line gives it, for a note."""
    words = ([f"--search {json.dumps(search, ensure_ascii=False)}"] if search else [])
    words += [f"--category {f}={v}" for f, values in (categories or {}).items() for v in values]
    return " ".join(words)


def _digest(bench: dict, ds: dict, values: dict) -> str:
    h = hashlib.sha256()
    definition = {k: v for k, v in bench.items() if not k.startswith("_") or k == "_version"}
    h.update(json.dumps([definition, ds, values], sort_keys=True, default=str).encode())
    here = bench.get("_here")
    for word in (bench.get("driver") or {}).get("command") or []:
        # the files the driver is started with, beside its definition: a change to its code lists again
        path = str(word).replace("{here}", here or "") if str(word).startswith("{here}/") else None
        if path and os.path.isfile(path):
            h.update(str(word).encode() + b"\0" + install.sha256_of(path).encode())
    return h.hexdigest()


def cache_path(bench: dict, ds: dict, values: dict) -> str:
    return os.path.join(paths.cache_dir(), FOLDER, bench["key"], _digest(bench, ds, values) + ".json")


def _start(bench: dict, ds: dict, values: dict, work: str) -> jsonl.Proc:
    try:
        up = install.upstream(bench)
        data = install.dataset(bench, ds, up)
        cmd, extra = install.driver_argv(bench)
    except (install.InstallFailed, OSError) as e:
        raise Refused(f"cannot prepare {bench['key']}'s dataset {ds['id']} for its listing: {e}")
    home, cwd = os.path.join(work, "driver-home"), os.path.join(work, "driver")
    os.makedirs(home)
    os.makedirs(cwd)
    env = run.base_env(home, {**extra, "NULLIUS_UPSTREAM": up or "", "NULLIUS_DATASET": data or "",
                              "NULLIUS_DATASET_ID": ds["id"], "PYTHONPATH": run.driver_path(),
                              "PYTHONDONTWRITEBYTECODE": "1", "NULLIUS_VERSION": str(bench.get("_version") or ""),
                              **run.option_env(values)})
    return jsonl.Proc(cmd, cwd, env, os.path.join(work, "driver.log")).start()


def _described(got: object, ids: List[str]) -> Dict[str, dict]:
    """``describe``'s reply checked: {"units": {ID: {"title", "text", "categories": {FIELD: VALUE}}}}."""
    units = got.get("units") if isinstance(got, dict) else None
    if not isinstance(units, dict):
        raise Refused("describe: the reply holds no units mapping")
    out = {}
    for u in ids:
        d = units.get(u) or {}
        cats = d.get("categories") if isinstance(d.get("categories"), dict) else {}
        out[u] = {"title": str(d.get("title") or ""), "text": str(d.get("text") or ""),
                  "categories": {str(k): str(v) for k, v in cats.items() if v is not None and str(v) != ""}}
    return out


def _list(bench: dict, ds: dict, values: dict) -> dict:
    folder = os.path.join(paths.cache_dir(), FOLDER)
    os.makedirs(folder, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".listing-", dir=folder)
    try:
        p = _start(bench, ds, values, work)
        try:
            ids = [str(u) for u in p.request("units", dataset=ds["id"], timeout=SECONDS).get("units") or []]
            said = (_described(p.request("describe", dataset=ds["id"], timeout=SECONDS), ids)
                    if (bench.get("driver") or {}).get("describe") else {})
        except jsonl.Failed as e:
            raise Refused(f"{bench['key']}'s driver could not list the dataset {ds['id']}: {e}")
        finally:
            p.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tasks = [{"id": u, **said.get(u, {"title": "", "text": "", "categories": {}})} for u in ids]
    return {"described": bool(said), "tasks": tasks}


def listing(bench: dict, dataset: str, options: Optional[dict] = None) -> dict:
    """{described, tasks: [{id, title, text, categories}]}: every task of the dataset under the options, in
    the driver's order, from the cache when it holds this listing."""
    ds = dataset_of(bench, dataset)
    values = option_values(bench, ds["id"], options)
    path = cache_path(bench, ds, values)
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict) and isinstance(got.get("tasks"), list):
            return got
    except (OSError, ValueError):
        pass
    got = _list(bench, ds, values)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".units-", suffix=".json", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(got, f, ensure_ascii=False)
    os.replace(tmp, path)
    return got


def _matches(task: dict, words: List[str]) -> bool:
    hay = " ".join((task["id"], task["title"], task["text"])).lower()
    return all(w in hay for w in words)


def find(bench: dict, dataset: str, options: Optional[dict] = None, search: str = "",
         categories: Optional[Dict[str, List[str]]] = None, page: int = 1, size: Optional[int] = PAGE) -> dict:
    """The tasks of ``dataset`` under ``options`` that the search text and the category values match, a page
    of ``size`` (all of them when None), page 1 first:
    {total, page, pages, of, described, tasks: [{id, title, text, categories}],
     categories: {FIELD: [{value, count}, ...]}}. ``total`` counts every match, ``of`` the dataset's tasks;
    each field's values are counted over the tasks the search and the other fields' filters match, the most
    first. A field no task has is refused; a page past the last is empty."""
    if not isinstance(page, int) or page < 1:
        raise Refused(f"page {page}: 1, 2, ...")
    got = listing(bench, dataset, options)
    tasks, chosen = got["tasks"], categories or {}
    fields = sorted({f for t in tasks for f in t["categories"]})
    unknown = [f for f in chosen if f not in fields]
    if unknown:
        raise Refused(f"--category {unknown[0]}: no such category"
                      + (f"; the categories are {', '.join(fields)}" if fields else "; these tasks have none"))
    words = [w for w in str(search or "").lower().split() if w]
    searched = [t for t in tasks if _matches(t, words)]

    def kept(t: dict, but: Optional[str] = None) -> bool:
        return all(t["categories"].get(f) in values for f, values in chosen.items() if f != but)

    matched = [t for t in searched if kept(t)]
    counts = {}
    for f in fields:
        n: Dict[str, int] = {}
        for t in searched:
            if f in t["categories"] and kept(t, but=f):
                n[t["categories"][f]] = n.get(t["categories"][f], 0) + 1
        counts[f] = [{"value": v, "count": c} for v, c in sorted(n.items(), key=lambda vc: (-vc[1], vc[0]))]
    per = size if size else max(1, len(matched))
    start = (page - 1) * per
    return {"total": len(matched), "page": page, "pages": max(1, -(-len(matched) // per)), "of": len(tasks),
            "described": got["described"], "tasks": matched[start:start + per], "categories": counts}
