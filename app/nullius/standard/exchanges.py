"""Each model exchange of a unit, kept whole: the request the relay sent and the reply that came back.

A unit's relay (``relay.Relay``) writes, beside its call record, under ``<record_dir>/exchanges/``:

- ``<tag>.jsonl``: one line per call it sent on an agent's route (``relay.agent_route``), its side calls
  included: ``n``, the call's number on its route (the call record's ``n`` too), ``t0``, ``path``,
  ``sent_sha256`` (the sha256 of the bytes sent), ``sent`` (the request after the relay's changes, its
  parts by hash), ``headers`` (the values of the headers sent, credentials left out) and ``reply`` (its
  status, whether it was streamed, its body as it arrived, as text);
- ``parts.jsonl``: each distinct part once, ``{"sha256": ..., "part": ...}``. A part is one item of
  ``messages`` or ``input``, one tool, or the system text (``system``, ``instructions``); its hash is the
  sha256 of its JSON. Inside a part, an inline base64 picture is ``{"blob": sha256, "type": ..., "bytes":
  ..., "as": "data_url"|"base64"}`` and its bytes are in
- ``blobs/<sha256>.<ext>``: each distinct picture once, as its bytes (png, jpeg, webp, gif).

What is stored is what was sent, the relay's key cut out of every string: a request that held the key
then no longer rebuilds to its hash, and its line says ``key_cut``. Nothing else is redacted, so a secret
an agent typed in a prompt or showed in a screenshot is in the store as it was sent. The modes
(``MODES``): ``whole``; ``text``, the same without the pictures' bytes (their hash, type and size stay);
``off``, no store. A unit's store is bounded (``max_mb``): once a call's new content would take it past
the bound, that call's line and every later one say ``cut``, and the call record goes on. Nothing here
stops or changes a reply: a failure is written in the call's line (``error``) when it can be, and dropped
when it cannot.

:func:`rebuild` gives a recorded request's bytes back from its line; :func:`new_parts`, the parts a call
holds that the call before did not.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import threading
from typing import Dict, List, Optional

MODES = ("whole", "text", "off")
DEFAULT = "whole"
MAX_MB = 256                                     # a unit's store, in millions of bytes
FOLDER = "exchanges"
PARTS = "parts.jsonl"
BLOBS = "blobs"
# the fields of a request kept by part: a list's items each a part, a text one part
PART_FIELDS = ("system", "instructions", "tools", "messages", "input")
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_DATA_URL = re.compile(r"data:(image/(?:png|jpeg|jpg|webp|gif));base64,")
MARK = frozenset(("blob", "type", "bytes", "as"))   # a picture's place in a stored part


class Missing(Exception):
    """A request that cannot be rebuilt from the store: the message says what is missing."""


class _AsSent(Exception):
    """A part that holds an object shaped like a picture's mark: it is kept as sent, its pictures inline."""


def _picture(text: str, media: Optional[str] = None) -> Optional[tuple]:
    """(type, bytes, form) of an inline picture: a data URL, or a Messages source's base64 ``data`` of
    type ``media``. None for any other text, and for one whose bytes would not encode back to it."""
    if media is None:
        m = _DATA_URL.match(text)
        if not m:
            return None
        kind, b64, form = m.group(1), text[m.end():], "data_url"
    elif media in EXT:
        kind, b64, form = media, text, "base64"
    else:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    # the alphabet alone, padding at the end only (validate): every whole group encodes back to itself, so the
    # text is the bytes' own encoding when its length is and its last group is
    if len(b64) != 4 * ((len(raw) + 2) // 3) or (raw and base64.b64encode(raw[-(len(raw) % 3 or 3):]).decode() != b64[-4:]):
        return None
    return kind, raw, form


def _mark(found: dict, kind: str, raw: bytes, form: str) -> dict:
    h = hashlib.sha256(raw).hexdigest()
    found[h] = (kind, raw)
    return {"blob": h, "type": kind, "bytes": len(raw), "as": form}


def _lift(v, found: dict):
    """``v`` with each inline picture replaced by its mark, the picture in ``found`` by its hash."""
    if isinstance(v, str):
        p = _picture(v) if v.startswith("data:image/") else None
        return _mark(found, *p) if p else v
    if isinstance(v, list):
        return [_lift(x, found) for x in v]
    if isinstance(v, dict):
        if set(v) == MARK:
            raise _AsSent()
        media = v.get("media_type") if v.get("type") == "base64" and isinstance(v.get("media_type"), str) else None
        out = {}
        for k, x in v.items():
            p = _picture(x, media) if media and k == "data" and isinstance(x, str) else None
            out[k] = _mark(found, *p) if p else _lift(x, found)
        return out
    return v


def _cut_key(v, keys):
    """``v`` with each of ``keys`` cut out of every string, a mapping's keys too."""
    if isinstance(v, str):
        for k in keys:
            v = v.replace(k, "[REDACTED]")
        return v
    if isinstance(v, list):
        return [_cut_key(x, keys) for x in v]
    if isinstance(v, dict):
        return {_cut_key(k, keys): _cut_key(x, keys) for k, x in v.items()}
    return v


def split(req: dict, found: dict, parts: dict) -> dict:
    """A request's ``sent``: each part by its hash, the rest as it was. ``parts`` gains each part's
    stored line by hash, ``found`` each picture's (type, bytes)."""
    sent = {}
    for k, v in req.items():
        if k not in PART_FIELDS:
            sent[k] = v
        elif isinstance(v, list):
            sent[k] = {"parts": [_part(x, found, parts) for x in v]}
        elif isinstance(v, str):
            sent[k] = {"part": _part(v, found, parts)}
        else:
            sent[k] = {"as_sent": v}
    return sent


def _part(x, found: dict, parts: dict) -> str:
    mine = {}
    try:
        text, as_sent = json.dumps(_lift(x, mine), separators=(",", ":")), False
    except _AsSent:
        text, as_sent, mine = json.dumps(x, separators=(",", ":")), True, {}
    h = hashlib.sha256(text.encode()).hexdigest()
    parts[h] = '{"sha256":"%s",%s"part":%s}' % (h, '"as_sent":true,' if as_sent else "", text)
    found.update(mine)
    return h


class Store:
    """One unit's store, written by its relay: the parts and pictures already written are known by
    their hashes, so a call that resends its context adds only what is new."""

    def __init__(self, record_dir: str, mode: str = DEFAULT, max_mb: int = MAX_MB, keys=()):
        if mode not in MODES or mode == "off":
            raise ValueError(f"exchanges {mode!r}: whole or text")
        if isinstance(max_mb, bool) or not isinstance(max_mb, int) or max_mb < 1:
            raise ValueError(f"exchanges_max_mb {max_mb!r}: a whole number of MB, at least 1")
        self.dir, self.mode, self.max_mb, self.bound = os.path.join(record_dir, FOLDER), mode, max_mb, max_mb * 10 ** 6
        self.keys = tuple(sorted({k for k in keys if k}, key=len, reverse=True))
        self.parts: set = set()
        self.blobs: set = set()
        self.size = 0                            # bytes written so far
        self.cut = False                         # the bound was met: every later call is cut
        self.lock = threading.Lock()

    def write(self, tag: str, n: int, t0: float, path: str, req: Optional[dict], raw: bytes, headers: Dict[str, str],
              status: int, streamed: bool, body: bytes) -> None:
        """One call's line, its new parts and pictures. ``req``: the request whose ``json.dumps`` is
        ``raw``, the bytes sent; None when the relay sent the body as it came (it is then kept whole as
        ``sent_body``). ``headers``: those sent, the credentials already left out. Never raises."""
        head = {"n": n, "t0": t0, "path": path, "sent_sha256": None}
        try:
            head["path"] = _cut_key(path, self.keys)
            head["sent_sha256"] = hashlib.sha256(raw).hexdigest()
            held = any(k.encode() in raw for k in self.keys)
            line = dict(head)
            found, parts = {}, {}
            if req is None:
                line["sent_body"] = _cut_key(raw.decode("utf-8", "replace"), self.keys)
                line["error"] = "the request's body is not a JSON object: it is kept whole, not by part"
            else:
                line["sent"] = split(_cut_key(req, self.keys) if held else req, found, parts)
            if held:
                line["key_cut"] = True
            line["headers"] = _cut_key(headers, self.keys)
            line["reply"] = {"status": status, "streamed": streamed, "body": _cut_key(body.decode("utf-8", "replace"), self.keys)}
            text = json.dumps(line)
            self._keep(tag, head, text, parts, found)
        except Exception as e:                   # nothing of the store may reach the caller's reply
            self._failed(tag, head, e)

    def _keep(self, tag: str, head: dict, text: str, parts: dict, found: dict) -> None:
        with self.lock:
            new_parts = "".join(p + "\n" for h, p in parts.items() if h not in self.parts)
            new_blobs = [(h, kind, raw) for h, (kind, raw) in found.items() if h not in self.blobs] if self.mode == "whole" else []
            if self.cut or self.size + len(new_parts) + sum(len(raw) for _, _, raw in new_blobs) + len(text) + 1 > self.bound:
                self.cut = True
                self._append(tag + ".jsonl", json.dumps(dict(head, cut=f"the store reached its bound of {self.max_mb} MB: "
                                                                        "this call and every later one are not kept")) + "\n")
                return
            for h, kind, raw in new_blobs:       # what a line names is written before it
                self._put(os.path.join(BLOBS, f"{h}.{EXT[kind]}"), raw)
                self.blobs.add(h)
            if new_parts:
                self._append(PARTS, new_parts)
                self.parts.update(parts)
            self._append(tag + ".jsonl", text + "\n")

    def _failed(self, tag: str, head: dict, e: Exception) -> None:
        try:
            said = _cut_key(f"{type(e).__name__}: {e}", self.keys)[:500]
            with self.lock:
                self._append(tag + ".jsonl", json.dumps(dict(head, error=said)) + "\n")
        except Exception:
            pass                                 # a full disk: the call record still says the call

    def _append(self, name: str, text: str) -> None:
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, name), "a") as f:
            f.write(text)
        self.size += len(text)                   # ASCII: json.dumps escapes the rest

    def _put(self, name: str, data: bytes) -> None:
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        self.size += len(data)


# -- reading a store ---------------------------------------------------------------
def _inside(folder: str, name: str) -> str:
    path = os.path.realpath(os.path.join(folder, name))
    if os.path.commonpath((folder, path)) != folder:
        raise Missing("the stored path resolves outside its folder")
    return path


def _digest(value) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise Missing("the stored digest must be 64 hexadecimal digits")
    return value


def _picture_path(folder: str, mark: dict) -> str:
    name = f"{_digest(mark['blob'])}.{EXT.get(mark['type'], 'bin')}"
    return _inside(os.path.join(os.path.realpath(folder), BLOBS), name)


def lines(folder: str, tag: str) -> List[dict]:
    """The lines of route ``tag`` in the store at ``folder`` (``<record_dir>/exchanges``); a line that
    cannot be read is left out."""
    return _jsonl(_inside(os.path.realpath(folder), tag + ".jsonl"))


def tags(folder: str) -> List[str]:
    """The routes a store holds lines of."""
    try:
        return sorted(n[:-6] for n in os.listdir(folder) if n.endswith(".jsonl") and n != PARTS)
    except OSError:
        return []


def _jsonl(path: str) -> List[dict]:
    out = []
    try:
        with open(path) as f:
            for row in f:
                try:
                    got = json.loads(row)
                except ValueError:
                    continue
                if isinstance(got, dict):
                    out.append(got)
    except OSError:
        pass
    return out


def stored_parts(folder: str) -> Dict[str, dict]:
    """Each part of a store by its hash: {"part": ..., "as_sent": bool}."""
    parts = {}
    for p in _jsonl(_inside(os.path.realpath(folder), PARTS)):
        h = _digest(p.get("sha256"))
        if not p.get("as_sent"):
            _drop(p.get("part"), lambda mark: _picture_path(folder, mark))
        parts[h] = {"part": p.get("part"), "as_sent": bool(p.get("as_sent"))}
    return parts


def _drop(v, picture):
    """A stored part with each picture's mark replaced by ``picture(mark)``."""
    if isinstance(v, dict):
        if set(v) == MARK:
            return picture(v)
        return {k: _drop(x, picture) for k, x in v.items()}
    if isinstance(v, list):
        return [_drop(x, picture) for x in v]
    return v


def rebuild(folder: str, line: dict, parts: Optional[Dict[str, dict]] = None) -> bytes:
    """The bytes a recorded call sent, put back together from its line, its parts and its pictures:
    what ``sent_sha256`` is the hash of when nothing was cut from them. Raises :class:`Missing` when
    the store does not hold the request."""
    if line.get("cut"):
        raise Missing(f"call {line.get('n')} was not kept: {line['cut']}")
    if "sent_body" in line:
        return line["sent_body"].encode()
    if not isinstance(line.get("sent"), dict):
        raise Missing(f"call {line.get('n')} has no request in the store" + (f": {line['error']}" if line.get("error") else ""))
    parts = stored_parts(folder) if parts is None else parts

    def part(h):
        _digest(h)
        if h not in parts:
            raise Missing(f"the part {h} is not in the store")
        got = parts[h]
        return got["part"] if got["as_sent"] else _drop(got["part"], picture)

    def picture(mark):
        name = _picture_path(folder, mark)
        try:
            with open(name, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            raise Missing(f"the picture {mark['blob']} is not in the store (a store kept as text holds no pictures)")
        return f"data:{mark['type']};base64,{b64}" if mark["as"] == "data_url" else b64

    req = {}
    for k, v in line["sent"].items():
        if k in PART_FIELDS and isinstance(v, dict) and set(v) == {"parts"}:
            req[k] = [part(h) for h in v["parts"]]
        elif k in PART_FIELDS and isinstance(v, dict) and set(v) == {"part"}:
            req[k] = part(v["part"])
        elif k in PART_FIELDS and isinstance(v, dict) and set(v) == {"as_sent"}:
            req[k] = v["as_sent"]
        else:
            req[k] = v
    return json.dumps(req).encode()


def check(folder: str, tag: str, n: int) -> dict:
    """Call ``n`` of route ``tag`` rebuilt: {"request": bytes or None, "sha256", "sent_sha256", "same",
    "why"}; ``why`` says what kept it from rebuilding or from matching."""
    line = next((x for x in lines(folder, tag) if x.get("n") == n), None)
    if line is None:
        raise Missing(f"no call {n} in the store of {tag}")
    try:
        got = rebuild(folder, line)
    except Missing as e:
        return {"request": None, "sha256": None, "sent_sha256": line.get("sent_sha256"), "same": False, "why": str(e)}
    sha = hashlib.sha256(got).hexdigest()
    same = sha == line.get("sent_sha256")
    why = None if same else ("the relay's key was cut out of the request" if line.get("key_cut")
                             else "the store's parts do not rebuild the bytes sent")
    return {"request": got, "sha256": sha, "sent_sha256": line.get("sent_sha256"), "same": same, "why": why}


def new_parts(line: dict, before: Optional[dict] = None) -> List[str]:
    """The parts call ``line`` holds that ``before``, the call before it, did not: their hashes, in the
    request's order."""
    def held(x):
        out = []
        for k, v in ((x or {}).get("sent") or {}).items():
            if k in PART_FIELDS and isinstance(v, dict):
                out += list(v.get("parts") or []) + ([v["part"]] if isinstance(v.get("part"), str) else [])
        return out
    seen = set(held(before))
    return [h for h in held(line) if h not in seen]
