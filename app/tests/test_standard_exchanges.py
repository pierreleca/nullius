"""The relay's store of each model exchange: the request as sent, by part, each
part and picture written once, and the reply as it arrived; what it keeps in
each mode and under its bound; that it never stands in a reply's way and never
holds the relay's key; and the request rebuilt to the bytes sent. The provider
is a fake endpoint on loopback; no key, no network."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from nullius.runs import cli as runs_cli, parse
from nullius.standard import cli as standard_cli, container, definitions, exchanges, install, pace as pacing, recipe, relay, run

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "standard", "catalogue")
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "sk-relay-real-0123456789abcdef"


def picture(i: int, size: int = 3000, kind: bytes = b"\x89PNG\r\n\x1a\n") -> bytes:
    """A picture's bytes, made up: a header and ``size`` bytes of its own."""
    seed = hashlib.sha256(str(i).encode()).digest()
    return kind + (seed * (size // len(seed) + 1))[:size]


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def data_url(raw: bytes, kind: str = "image/png") -> str:
    return f"data:{kind};base64,{b64(raw)}"


PNG, JPEG = picture(1), picture(2, kind=b"\xff\xd8\xff\xe0")
TOOL = {"type": "object", "properties": {"x": {"type": "integer"}}}
# each API shape with inline pictures in its own form: (path, request, the pictures it holds as (bytes, extension))
SHAPES = {
    "chat": ("/chat/completions",
             {"model": "m", "max_tokens": 100, "messages": [
                 {"role": "system", "content": "be brief"},
                 {"role": "user", "content": [{"type": "text", "text": "look"},
                                              {"type": "image_url", "image_url": {"url": data_url(PNG)}}]},
                 {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                                        "function": {"name": "click", "arguments": "{\"x\": 1}"}}]},
                 {"role": "tool", "tool_call_id": "c1", "content": [{"type": "image_url", "image_url": {"url": data_url(JPEG, "image/jpeg")}}]}],
              "tools": [{"type": "function", "function": {"name": "click", "parameters": TOOL}}]},
             [(PNG, "png"), (JPEG, "jpg")]),
    "messages": ("/messages",
                 {"model": "m", "max_tokens": 100, "system": "be brief",
                  "tools": [{"name": "click", "input_schema": TOOL}],
                  "messages": [
                      {"role": "user", "content": [{"type": "text", "text": "look"},
                                                   {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(PNG)}}]},
                      {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "click", "input": {"x": 1}}]},
                      {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
                          {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(JPEG)}}]}]}]},
                 [(PNG, "png"), (JPEG, "jpg")]),
    "responses": ("/responses",
                  {"model": "m", "instructions": "be brief", "tools": [{"type": "function", "name": "click", "parameters": TOOL}],
                   "input": [{"role": "user", "content": [{"type": "input_text", "text": "look"},
                                                          {"type": "input_image", "image_url": data_url(PNG), "detail": "auto"}]},
                             {"type": "function_call", "call_id": "c1", "name": "click", "arguments": "{\"x\": 1}"},
                             {"type": "function_call_output", "call_id": "c1", "output": "clicked"},
                             {"role": "user", "content": [{"type": "input_image", "image_url": data_url(JPEG, "image/jpeg")}]}]},
                  [(PNG, "png"), (JPEG, "jpg")]),
}

# the replies, each with a reasoning text, streamed and not
CHAT = {"choices": [{"message": {"role": "assistant", "content": "ok", "reasoning": "I looked"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
CHAT_SSE = (b'data: {"choices":[{"delta":{"reasoning":"I "}}]}\n\n'
            b'data: {"choices":[{"delta":{"reasoning":"looked"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
            b'data: [DONE]\n\n')
MESSAGE = {"type": "message", "role": "assistant", "stop_reason": "end_turn",
           "content": [{"type": "thinking", "thinking": "I looked", "signature": "s"}, {"type": "text", "text": "ok"}],
           "usage": {"input_tokens": 5, "output_tokens": 2}}
MESSAGE_SSE = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":5,"output_tokens":1}}}\n\n'
               b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n'
               b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"I looked"}}\n\n'
               b'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}\n\n'
               b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"ok"}}\n\n'
               b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
               b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
RESPONSE = {"object": "response", "status": "completed",
            "output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "I looked"}]},
                       {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 9, "output_tokens": 2}}
RESPONSE_SSE = (b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                b'event: response.completed\ndata: ' + json.dumps({"type": "response.completed", "response": RESPONSE}).encode() + b'\n\n')


class Upstream(BaseHTTPRequestHandler):
    """A model: each body as it arrived is kept, and every reply holds a reasoning text."""
    seen = []                                    # (path, headers, the body's bytes)
    refuse = 0                                   # how many calls, from now, get a rate limit

    def log_message(self, *a):
        pass

    def do_GET(self):                            # no list of models, as a provider without one
        self.reply(404, b"", "application/json")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        Upstream.seen.append((self.path, dict(self.headers), raw))
        try:
            body = json.loads(raw)
        except ValueError:
            return self.reply(400, b'{"error":{"message":"not JSON"}}', "application/json")
        if Upstream.refuse > 0:
            Upstream.refuse -= 1
            return self.reply(429, b'{"error":{"message":"Rate limit reached for requests"}}', "application/json")
        stream = body.get("stream")
        if self.path.endswith("/messages"):
            return self.reply(200, MESSAGE_SSE, "text/event-stream") if stream else self.reply(200, json.dumps(MESSAGE).encode(), "application/json")
        if self.path.endswith("/responses"):
            return self.reply(200, RESPONSE_SSE, "text/event-stream") if stream else self.reply(200, json.dumps(RESPONSE).encode(), "application/json")
        if stream:
            return self.reply(200, CHAT_SSE, "text/event-stream")
        said = body["messages"][-1]["content"] if isinstance(body.get("messages"), list) and body["messages"] else ""
        got = dict(CHAT, choices=[{"message": dict(CHAT["choices"][0]["message"],
                                                   content="you said: " + (said if isinstance(said, str) else "pictures"))}])
        return self.reply(200, json.dumps(got).encode(), "application/json")

    def reply(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WithUpstream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.up.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.up.shutdown()
        cls.up.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        Upstream.refuse = 0

    def relay(self, mode="whole", **kw):
        r = relay.Relay(self.base, KEY, os.path.join(self.tmp, f"rec-{len(os.listdir(self.tmp))}"), exchanges=mode, **kw).start()
        self.addCleanup(r.stop)
        return r

    def post(self, r, tag, path, body, headers=None, raw=None):
        req = urllib.request.Request(r.url(tag) + path, data=raw if raw is not None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + relay.PLACEHOLDER_KEY,
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req) as got:
                return got.status, got.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    @staticmethod
    def store(r):
        return os.path.join(r.dir, exchanges.FOLDER)

    @staticmethod
    def files(folder):
        out = {}
        for root, _, names in os.walk(folder):
            for n in names:
                with open(os.path.join(root, n), "rb") as f:
                    out[os.path.relpath(os.path.join(root, n), folder)] = f.read()
        return out


class StoreFiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def store(self, mode="whole"):
        return exchanges.Store(tempfile.mkdtemp(dir=self.root), mode=mode, keys=(KEY,))

    def keep(self, store, req, n=1, headers=None):
        store.write("u0001", n, 1.0, "/messages", req, json.dumps(req).encode(), headers or {}, 200, False, b"{}")

    def assert_picture_rejected(self, store, line, parts):
        for read in (lambda: exchanges.stored_parts(store.dir),
                     lambda: exchanges.rebuild(store.dir, line),
                     lambda: exchanges.rebuild(store.dir, line, parts)):
            with self.assertRaises(exchanges.Missing):
                read()
        result = exchanges.check(store.dir, "u0001", 1)
        self.assertFalse(result["same"])
        self.assertIsNone(result["request"])

    def test_picture_names_are_only_sha256_digests_in_every_part_reader(self):
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside picture")
        for name in (str(outside.with_suffix("")), "../../outside", "a" * 63, "a" * 65,
                     "g" * 64, "a" * 64 + "\n", "a" * 64 + ".png", None):
            with self.subTest(name=name):
                store = self.store()
                self.keep(store, {"messages": [data_url(PNG)]})
                line, = exchanges.lines(store.dir, "u0001")
                parts = exchanges.stored_parts(store.dir)
                part, = parts.values()
                part["part"]["blob"] = name
                (Path(store.dir) / exchanges.PARTS).write_text("".join(
                    json.dumps(dict(sha256=h, **p)) + "\n" for h, p in parts.items()))
                self.assert_picture_rejected(store, line, parts)

    def test_picture_links_must_resolve_inside_the_stores_own_blobs_folder(self):
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside picture")
        for linked in ("file", "directory"):
            with self.subTest(linked=linked):
                store = self.store()
                req = {"messages": [data_url(PNG)]}
                self.keep(store, req)
                line, = exchanges.lines(store.dir, "u0001")
                parts = exchanges.stored_parts(store.dir)
                blobs = Path(store.dir) / exchanges.BLOBS
                blob, = blobs.iterdir()
                if linked == "file":
                    blob.unlink()
                    blob.symlink_to(outside)
                else:
                    moved = self.root / "moved"
                    blobs.rename(moved)
                    blobs.symlink_to(moved, target_is_directory=True)
                self.assert_picture_rejected(store, line, parts)

        store = self.store()
        self.keep(store, req)
        blobs = Path(store.dir) / exchanges.BLOBS
        blob, = blobs.iterdir()
        kept = blobs / ("0" * 64 + ".png")
        blob.rename(kept)
        blob.symlink_to(kept)
        self.assertTrue(exchanges.check(store.dir, "u0001", 1)["same"])

    def test_part_and_call_files_cannot_be_read_through_outside_links(self):
        for name, read in ((exchanges.PARTS, exchanges.stored_parts),
                           ("u0001.jsonl", lambda folder: exchanges.lines(folder, "u0001"))):
            with self.subTest(name=name):
                store = self.store()
                self.keep(store, {"messages": ["hello"]})
                path = Path(store.dir) / name
                outside = self.root / name
                path.rename(outside)
                path.symlink_to(outside)
                with self.assertRaises(exchanges.Missing):
                    read(store.dir)


class ByteForByte(WithUpstream):
    def test_each_shape_rebuilds_to_the_bytes_sent_streamed_or_not_and_a_changed_part_fails(self):
        for shape, (path, ask, pictures) in SHAPES.items():
            for stream in (False, True):
                with self.subTest(shape=shape, stream=stream):
                    r = self.relay()
                    status, reply = self.post(r, "u0001", path, dict(ask, **({"stream": True} if stream else {})))
                    self.assertEqual(status, 200)
                    sent = Upstream.seen[-1][2]
                    store = self.store(r)
                    (line,) = exchanges.lines(store, "u0001")
                    self.assertEqual((line["n"], r.calls("u0001")[-1]["n"]), (1, 1))     # the record and the line, one number
                    self.assertEqual(line["sent_sha256"], hashlib.sha256(sent).hexdigest())
                    self.assertEqual(exchanges.rebuild(store, line), sent)
                    self.assertTrue(exchanges.check(store, "u0001", 1)["same"])
                    # each picture a file of its own, its bytes as sent; no part holds its text
                    self.assertEqual(sorted(n for n in os.listdir(os.path.join(store, exchanges.BLOBS))),
                                     sorted(f"{hashlib.sha256(p).hexdigest()}.{ext}" for p, ext in pictures))
                    for p, ext in pictures:
                        with open(os.path.join(store, exchanges.BLOBS, f"{hashlib.sha256(p).hexdigest()}.{ext}"), "rb") as f:
                            self.assertEqual(f.read(), p)
                    texts = self.files(store)
                    self.assertFalse(any(b64(p).encode() in texts[n] for p, _ in pictures for n in texts if not n.startswith(exchanges.BLOBS)))
                    # the reply as it arrived, its reasoning read back
                    self.assertEqual((line["reply"]["status"], line["reply"]["streamed"], line["reply"]["body"].encode()), (200, stream, reply))
                    self.assertEqual(relay.read_reply(path, line["reply"]["body"].encode(), stream, reasoning=True)["reasoning"], "I looked")
                    # a changed part: its bytes no longer the ones sent
                    parts = os.path.join(store, exchanges.PARTS)
                    with open(parts) as f:
                        text = f.read()
                    with open(parts, "w") as f:
                        f.write(text.replace('"look"', '"LOOK"'))
                    got = exchanges.check(store, "u0001", 1)
                    self.assertFalse(got["same"])
                    self.assertIn("do not rebuild", got["why"])

    def test_a_picture_whose_text_is_not_its_bytes_own_encoding_stays_in_its_part(self):
        """Padding bits set, padding missing, a line break, an object shaped like a picture's mark: kept as sent,
        and the request still rebuilds to its bytes. The one canonical picture is a file."""
        odd = ["data:image/png;base64,QR==", "data:image/png;base64,QQ", "data:image/png;base64,QQ==\n", "data:image/bmp;base64,QQ=="]
        ask = {"model": "m", "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": u}} for u in odd + ["data:image/png;base64,QQ=="]]},
            {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QR=="}}]},
            {"role": "user", "content": [{"blob": "x", "type": "t", "bytes": 1, "as": "data_url"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUI="}}]}]}
        r = self.relay()
        self.assertEqual(self.post(r, "u0001", "/chat/completions", ask)[0], 200)
        store = self.store(r)
        (line,) = exchanges.lines(store, "u0001")
        self.assertEqual(exchanges.rebuild(store, line), Upstream.seen[-1][2])
        self.assertEqual(os.listdir(os.path.join(store, exchanges.BLOBS)), [hashlib.sha256(b"A").hexdigest() + ".png"])
        self.assertEqual(sum('"as_sent":true' in x for x in open(os.path.join(store, exchanges.PARTS))), 1)

    def test_the_relays_changes_are_what_is_kept(self):
        """``sent`` is the request after the relay's changes: the model it forces, a stream's usage option."""
        r = self.relay(model="m-run")
        self.post(r, "u0001", "/chat/completions", dict(SHAPES["chat"][1], model="asked", stream=True))
        (line,) = exchanges.lines(self.store(r), "u0001")
        self.assertEqual((line["sent"]["model"], line["sent"]["stream_options"]), ("m-run", {"include_usage": True}))
        self.assertEqual(exchanges.rebuild(self.store(r), line), Upstream.seen[-1][2])


class StoredOnce(WithUpstream):
    def test_a_unit_that_resends_its_context_writes_each_part_and_picture_once(self):
        r = self.relay()
        history = [{"role": "system", "content": "be brief"}]
        sizes = 0
        for i in range(4):
            history.append({"role": "user", "content": [{"type": "text", "text": f"step {i}"},
                                                        {"type": "image_url", "image_url": {"url": data_url(picture(10 + i, 20_000))}}]})
            body = {"model": "m", "messages": history, "tools": [{"type": "function", "function": {"name": "click", "parameters": TOOL}}]}
            self.assertEqual(self.post(r, "u0003", "/chat/completions", body)[0], 200)
            sizes += len(Upstream.seen[-1][2])
            history.append({"role": "assistant", "content": f"did {i}"})
        store = self.store(r)
        parts = [json.loads(x) for x in open(os.path.join(store, exchanges.PARTS))]
        hashes = [p["sha256"] for p in parts]
        self.assertEqual(len(hashes), len(set(hashes)))
        self.assertEqual(len(hashes), 1 + 4 + 3 + 1)          # the system text, four asks, three answers, the tool
        self.assertEqual(len(os.listdir(os.path.join(store, exchanges.BLOBS))), 4)
        lines = exchanges.lines(store, "u0003")
        self.assertEqual([x["n"] for x in lines], [1, 2, 3, 4])
        for x in lines:                                       # a call's line lists hashes alone
            self.assertEqual(set(x["sent"]["messages"]), {"parts"})
            self.assertTrue(all(len(h) == 64 and h in hashes for h in x["sent"]["messages"]["parts"]))
            self.assertTrue(exchanges.check(store, "u0003", x["n"])["same"])
        self.assertEqual(len(exchanges.new_parts(lines[1], lines[0])), 2)    # the answer before it, and the new ask
        self.assertEqual(exchanges.new_parts(lines[0]), lines[0]["sent"]["messages"]["parts"] + lines[0]["sent"]["tools"]["parts"])
        kept = sum(len(v) for v in self.files(store).values())
        self.assertLess(kept, sizes / 2)


class TextOnly(WithUpstream):
    def test_text_keeps_each_pictures_hash_type_and_size_and_none_of_its_bytes(self):
        r = self.relay("text")
        path, ask, pictures = SHAPES["messages"]
        self.assertEqual(self.post(r, "u0001", path, ask)[0], 200)
        store = self.store(r)
        self.assertFalse(os.path.exists(os.path.join(store, exchanges.BLOBS)))
        texts = self.files(store)
        for p, _ in pictures:
            self.assertFalse(any(b64(p).encode() in t for t in texts.values()))
            self.assertIn(json.dumps({"blob": hashlib.sha256(p).hexdigest(), "type": "image/png" if p is PNG else "image/jpeg",
                                      "bytes": len(p), "as": "base64"}, separators=(",", ":")).encode(), texts[exchanges.PARTS])
        got = exchanges.check(store, "u0001", 1)
        self.assertFalse(got["same"])
        self.assertIn("kept as text holds no pictures", got["why"])


class Bound(WithUpstream):
    def test_past_its_bound_a_units_lines_say_cut_and_its_calls_go_on_unchanged(self):
        r = self.relay(exchanges_max_mb=1)
        replies = []
        for i in range(5):
            content = [{"type": "image_url", "image_url": {"url": data_url(picture(100 + i, 300_000))}}] if i < 4 else "a last word"
            status, reply = self.post(r, "u0002", "/chat/completions", {"model": "m", "messages": [{"role": "user", "content": content}]})
            replies.append((status, json.loads(reply)["choices"][0]["message"]["content"]))
        self.assertEqual(replies, [(200, "you said: pictures")] * 4 + [(200, "you said: a last word")])
        lines = exchanges.lines(self.store(r), "u0002")
        self.assertEqual([x["n"] for x in lines], [1, 2, 3, 4, 5])
        self.assertEqual([bool(x.get("cut")) for x in lines], [False, False, False, True, True])   # the small last one too
        self.assertIn("bound of 1 MB", lines[3]["cut"])
        self.assertEqual([(c["n"], c["status"]) for c in r.calls("u0002")], [(n, 200) for n in range(1, 6)])
        self.assertLessEqual(sum(len(v) for v in self.files(self.store(r)).values()), 10 ** 6 + 2000)
        self.assertIn("not kept", exchanges.check(self.store(r), "u0002", 4)["why"])


class Off(WithUpstream):
    def test_off_keeps_no_store_and_the_call_record_of_before(self):
        ask = SHAPES["chat"][1]
        off, whole, default = self.relay("off"), self.relay("whole"), relay.Relay(self.base, KEY, os.path.join(self.tmp, "default")).start()
        self.addCleanup(default.stop)
        for r in (off, whole, default):
            self.assertEqual(self.post(r, "u0001", "/chat/completions", ask)[0], 200)
        self.assertFalse(os.path.exists(self.store(off)))
        self.assertFalse(os.path.exists(self.store(default)))      # the relay's own default: off
        timeless = lambda c: {k: v for k, v in c.items() if k not in ("t0", "t1")}
        (a,), (b,), (c,) = off.calls("u0001"), whole.calls("u0001"), default.calls("u0001")
        self.assertNotIn("n", a)
        self.assertEqual(timeless(a), timeless(c))
        self.assertEqual(timeless(a), {k: v for k, v in timeless(b).items() if k != "n"})   # the store adds n alone
        self.assertEqual(set(a), {"t0", "t1", "path", "model", "model_asked", "status", "request", "headers", "reply"})

    def test_the_judges_and_the_simulated_users_routes_keep_the_record_alone(self):
        r = self.relay()
        for tag in ("u0001-judge", "u0001-simulator"):
            self.assertEqual(self.post(r, tag, "/chat/completions", SHAPES["chat"][1])[0], 200)
            self.assertNotIn("n", r.calls(tag)[-1])
        self.assertEqual(exchanges.tags(self.store(r)), [])


class NeverInTheWay(WithUpstream):
    def test_a_full_disk_neither_changes_nor_stops_the_reply_and_the_line_says_what_failed(self):
        path, ask, _ = SHAPES["chat"]
        clean = self.relay("off")
        expected = self.post(clean, "u0001", path, ask)
        r = self.relay()
        full = OSError(28, "No space left on device")
        with mock.patch.object(exchanges.Store, "_put", side_effect=full):
            self.assertEqual(self.post(r, "u0001", path, ask), expected)
        (line,) = exchanges.lines(self.store(r), "u0001")
        self.assertIn("No space left on device", line["error"])
        with mock.patch.object(exchanges.Store, "_put", side_effect=full), mock.patch.object(exchanges.Store, "_append", side_effect=full):
            self.assertEqual(self.post(r, "u0001", path, ask), expected)      # nothing of the store written: the reply still whole
        self.assertEqual([c["n"] for c in r.calls("u0001")], [1, 2])          # and the call record goes on
        self.assertEqual(len(exchanges.lines(self.store(r), "u0001")), 1)
        self.assertEqual(self.post(r, "u0001", path, ask), expected)          # room again: the next call is kept whole
        self.assertTrue(exchanges.check(self.store(r), "u0001", 3)["same"])

    def test_a_part_that_cannot_be_split_and_a_body_that_is_not_json_are_said_in_the_line(self):
        r = self.relay()
        path, ask, _ = SHAPES["chat"]
        with mock.patch.object(exchanges, "split", side_effect=TypeError("a part that is not JSON")):
            status, reply = self.post(r, "u0001", path, ask)
        self.assertEqual((status, json.loads(reply)["choices"][0]["message"]["content"]), (200, "you said: pictures"))
        status, reply = self.post(r, "u0001", path, None, raw=b"not json at all")
        self.assertEqual((status, reply), (400, b'{"error":{"message":"not JSON"}}'))
        first, second = exchanges.lines(self.store(r), "u0001")
        self.assertEqual(first["error"], "TypeError: a part that is not JSON")
        self.assertIn("not a JSON object", second["error"])
        self.assertEqual(second["sent_body"], "not json at all")
        self.assertTrue(exchanges.check(self.store(r), "u0001", 2)["same"])        # kept whole, it still rebuilds


class NoKey(WithUpstream):
    def test_the_relays_key_is_in_no_file_of_the_store(self):
        r = self.relay()
        ask = dict(SHAPES["messages"][1], system=f"the key is {KEY}, keep it",
                   tools=[{"name": "click", "description": f"say {KEY}", "input_schema": TOOL}])
        status, _ = self.post(r, "u0001", "/messages?k=" + KEY, ask, headers={"X-Trace-Id": KEY, "Authorization": "Bearer " + KEY})
        self.assertEqual(status, 200)
        self.assertIn(KEY.encode(), Upstream.seen[-1][2])                 # the agent sent it, and the relay sent it on
        for name, data in self.files(self.store(r)).items():
            self.assertNotIn(KEY.encode(), data, name)
        (line,) = exchanges.lines(self.store(r), "u0001")
        self.assertTrue(line["key_cut"])
        self.assertEqual({k.lower(): v for k, v in line["headers"].items()}["x-trace-id"], "[REDACTED]")
        self.assertFalse(any(k.lower() in ("authorization", "x-api-key") for k in line["headers"]))
        got = exchanges.check(self.store(r), "u0001", 1)
        self.assertFalse(got["same"])
        self.assertIn("key was cut out", got["why"])


class TheSidecar(WithUpstream):
    def test_the_sidecars_arguments_hold_both_values(self):
        argv = container.relay_argv("img", name="r", job="j", app="/app", record="/rec", key_file="/k", base="b", model="m", routes={},
                                    exchanges="text", exchanges_max_mb=64)
        self.assertEqual(argv[argv.index("--exchanges") + 1:argv.index("--exchanges") + 4], ["text", "--exchanges-max-mb", "64"])
        unit = container.Unit("job", "u0", os.path.join(self.tmp, "rec"), image="img", app="/app", base="b", key="k", model="m",
                              exchanges="whole", exchanges_max_mb=32)
        ran = []

        def docker(args, **kw):
            ran.append(args)
            if args[:1] == ["run"]:
                raise container.Unavailable("no engine in this test")
            return subprocess.CompletedProcess(args, 0, "", "")
        with mock.patch.object(container, "_docker", docker), self.assertRaises(container.Unavailable):
            unit.start()
        (started,) = [a for a in ran if a[:1] == ["run"]]
        self.assertEqual(started[started.index("--exchanges") + 1:started.index("--exchanges") + 4], ["whole", "--exchanges-max-mb", "32"])

    def test_the_relays_own_command_takes_them(self):
        """The sidecar's process: given ``text``, it keeps the call and none of its pictures' bytes."""
        key, record = os.path.join(self.tmp, "key"), os.path.join(self.tmp, "rec")
        with open(key, "w") as f:
            f.write(KEY)
        command = [sys.executable, "-m", "nullius.standard.relay", "serve", "--listen", "127.0.0.1:0", "--base", self.base,
                   "--record", record, "--key-file", key]
        env = dict(os.environ, PYTHONPATH=APP)
        bad = subprocess.run(command + ["--exchanges-max-mb", "0"], cwd=APP, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("at least 1", bad.stdout)
        p = subprocess.Popen(command + ["--exchanges", "text", "--exchanges-max-mb", "8"], cwd=APP, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            at = json.loads(p.stdout.readline())["listening"]
            path, ask, _ = SHAPES["messages"]
            req = urllib.request.Request(f"{at}/u0001/v1{path}", data=json.dumps(ask).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as got:
                self.assertEqual(got.status, 200)
        finally:
            p.terminate()
            p.wait(timeout=20)
            p.stdout.close()
        store = os.path.join(record, exchanges.FOLDER)
        self.assertEqual([x["n"] for x in exchanges.lines(store, "u0001")], [1])
        self.assertFalse(os.path.exists(os.path.join(store, exchanges.BLOBS)))


class TheRun(WithUpstream):
    """A whole run of the fake agent, its first call refused: a retried try's store goes aside with it."""

    def setUp(self):
        super().setUp()
        self._home = install.HOME
        install.HOME = os.path.join(self.tmp, "home")
        self.addCleanup(setattr, install, "HOME", self._home)
        self.addCleanup(setattr, pacing, "FIRST", pacing.FIRST)
        pacing.FIRST = 0.5
        self.env = {"MODEL_API_URL": self.base, "MODEL_API_KEY": KEY}

    def go(self, **kw):
        b = dict(definitions.get("benchmark", "fakebench", ROOT), datasets=[{"id": "responding", "units": 3}])
        return run.run(b, definitions.get("agent", "fake", ROOT), model="m-1", provider="custom", jobs_root=os.path.join(self.tmp, "jobs"),
                       concurrency=1, env=self.env, say=lambda s: None, unsandboxed=True, dataset="responding", **kw)

    def test_a_retried_try_keeps_its_store_and_the_next_try_starts_an_empty_one(self):
        Upstream.refuse = 1
        job_dir = self.go(retries=1)
        (kept,) = os.listdir(os.path.join(job_dir, "retried"))
        name = kept.rpartition(".try-")[0]
        tag = name.rsplit("__", 1)[1]
        before = os.path.join(job_dir, "retried", kept, ".work", "relay", exchanges.FOLDER)
        now = os.path.join(job_dir, name, ".work", "relay", exchanges.FOLDER)
        self.assertEqual([(x["n"], x["reply"]["status"]) for x in exchanges.lines(before, tag)], [(1, 429)])
        self.assertEqual([(x["n"], x["reply"]["status"]) for x in exchanges.lines(now, tag)], [(1, 200)])
        self.assertTrue(exchanges.check(now, tag, 1)["same"])          # its own parts: nothing read from the try before
        with open(os.path.join(job_dir, "config.json")) as f:
            said = json.load(f)["standard"]
        self.assertEqual((said["exchanges"], said["exchanges_max_mb"]), ("whole", 256))
        for root, _, names in os.walk(job_dir):
            for n in names:
                with open(os.path.join(root, n), "rb") as f:
                    self.assertNotIn(KEY.encode(), f.read(), os.path.join(root, n))

        # the command: the request rebuilt, on stdout as the bytes sent, the verdict on stderr
        (j,) = parse.load_all([os.path.join(self.tmp, "jobs")])
        sent = [raw for _, _, raw in Upstream.seen if b"model hi" in raw][-1]
        code, out, err = self.show(j.job_id, name, "--call", "1", "--request")
        self.assertEqual((code, out), (0, sent + b"\n"))
        self.assertIn("the recorded sent_sha256", err)
        with open(os.path.join(now, exchanges.PARTS)) as f:
            text = f.read()
        with open(os.path.join(now, exchanges.PARTS), "w") as f:
            f.write(text.replace("model hi", "model HI"))
        code, out, err = self.show(j.job_id, name, "--call", "1", "--request")
        self.assertEqual(code, runs_cli.EXIT_FAILED)
        self.assertIn("not the recorded sent_sha256", err)
        self.assertEqual(self.show(j.job_id, name, "--call", "9", "--request")[0], runs_cli.EXIT_NOT_FOUND)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as usage:
            runs_cli.main(["show", os.path.join(self.tmp, "jobs"), j.job_id, name, "--call", "1"])
        self.assertEqual(usage.exception.code, 2)

    def show(self, job_id, trial, *more):
        out, err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8"), io.StringIO()
        with mock.patch.object(sys, "stdout", out), contextlib.redirect_stderr(err):
            code = runs_cli.main(["show", os.path.join(self.tmp, "jobs"), job_id, trial, *more])
        out.flush()
        return code, out.buffer.getvalue(), err.getvalue()

    def test_off_and_a_bad_value_and_the_command_line(self):
        job_dir = self.go(exchanges="off", name="off")
        for root, dirs, _ in os.walk(job_dir):
            self.assertNotIn(exchanges.FOLDER, dirs, root)
        with open(os.path.join(job_dir, "config.json")) as f:
            self.assertEqual(json.load(f)["standard"]["exchanges"], "off")
        for bad in ({"exchanges": "all"}, {"exchanges_max_mb": 0}, {"exchanges_max_mb": True}, {"exchanges_max_mb": 1.5}):
            with self.subTest(bad=bad), self.assertRaises(run.Refused):
                self.go(name="bad", **bad)
        given = {}
        b, o = definitions.get("benchmark", "fakebench", ROOT), definitions.get("agent", "fake", ROOT)
        with mock.patch.object(standard_cli, "_bench", return_value=b), mock.patch.object(standard_cli, "_other", return_value=o), \
                mock.patch.object(standard_cli, "licences", return_value=(None, None)), mock.patch.object(standard_cli, "_exit_on_term"), \
                mock.patch.object(standard_cli.run, "run_and_report", side_effect=lambda *a, **kw: given.update(kw) or ("job", False)), \
                mock.patch.object(standard_cli.progress, "ECHO", None), contextlib.redirect_stdout(io.StringIO()):
            standard_cli.main(["run", "fakebench", "fake", "--model", "m-1", "--from-run"])
            self.assertEqual((given["exchanges"], given["exchanges_max_mb"]), ("whole", 256))
            standard_cli.main(["run", "fakebench", "fake", "--model", "m-1", "--from-run", "--exchanges", "text", "--exchanges-max-mb", "64"])
            self.assertEqual((given["exchanges"], given["exchanges_max_mb"]), ("text", 64))


class Recipe(unittest.TestCase):
    def test_a_recipe_takes_them_and_says_them_only_when_not_the_default(self):
        defs = {k: definitions.load(k, ROOT)[0] for k in ("benchmark", "agent", "capability")}
        base = {"version": 1, "harness": "standard", "name": "fake-0101-120000", "title": "t", "benchmark": "fakebench", "dataset": "all",
                "other": "fake", "provider": "openai", "model": "gpt-5-mini", "limit": 2, "concurrency": 2, "unsandboxed": True, "where": "local"}
        out = recipe.compile(base, defs)
        self.assertEqual(out["errors"], [])
        self.assertNotIn("--exchanges", out["script"])
        out = recipe.compile(dict(base, exchanges="text", exchanges_max_mb=64), defs)
        self.assertEqual(out["errors"], [])
        self.assertIn("--exchanges text --exchanges-max-mb 64 ", out["script"])
        self.assertIn(["Model exchanges", "text · at most 64 MB per task"], [row[1:] for row in out["review"]])
        for bad in ({"exchanges": "all"}, {"exchanges_max_mb": 0}, {"exchanges_max_mb": "x"}):
            errors = recipe.compile(dict(base, **bad), defs)["errors"]
            self.assertEqual([e["step"] for e in errors], [recipe.STEP["limits"]], bad)


class Reasoning(unittest.TestCase):
    def test_read_reply_gives_the_reasoning_only_when_asked(self):
        for path, body, streamed in (("/chat/completions", json.dumps(CHAT).encode(), False), ("/chat/completions", CHAT_SSE, True),
                                     ("/messages", json.dumps(MESSAGE).encode(), False), ("/messages", MESSAGE_SSE, True),
                                     ("/responses", json.dumps(RESPONSE).encode(), False), ("/responses", RESPONSE_SSE, True)):
            with self.subTest(path=path, streamed=streamed):
                got = relay.read_reply(path, body, streamed, reasoning=True)
                self.assertEqual((got["text"], got["reasoning"]), ("ok", "I looked"))
                self.assertNotIn("reasoning", relay.read_reply(path, body, streamed))     # the call record's reading, unchanged
        thought = {"choices": [{"message": {"content": "ok", "reasoning_content": "deep"}}]}
        self.assertEqual(relay.read_reply("/chat/completions", json.dumps(thought).encode(), False, reasoning=True)["reasoning"], "deep")


if __name__ == "__main__":
    unittest.main()
