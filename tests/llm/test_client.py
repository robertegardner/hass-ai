import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pae.llm.client import LLMError, OllamaClient


class FakeOllama:
    """Scriptable /api/chat: each queued item is a JSON-able content dict,
    the string 'garbage' (non-JSON content), or an int HTTP status to fail with."""

    def __init__(self):
        self.responses = []
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                item = outer.responses.pop(0) if outer.responses else 500
                if isinstance(item, int):
                    self.send_response(item)
                    self.end_headers()
                    return
                content = "garbage{" if item == "garbage" else json.dumps(item)
                payload = json.dumps({"message": {"role": "assistant", "content": content}})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    servers = []

    def _make():
        s = FakeOllama()
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
MSGS = [{"role": "user", "content": "hi"}]


def client(primary, fallback, **kw):
    return OllamaClient(
        primary_url=primary.url,
        fallback_url=fallback.url,
        primary_model="m-primary",
        fallback_model="m-fallback",
        timeout_seconds=5.0,
        **kw,
    )


def test_happy_path_uses_primary(fake):
    p, f = fake(), fake()
    p.responses = [{"ok": True}]
    resp = client(p, f).chat_json(MSGS, SCHEMA)
    assert resp.content == {"ok": True}
    assert resp.model == "m-primary"
    path, body = p.requests[0]
    assert path == "/api/chat"
    assert body["format"] == SCHEMA and body["stream"] is False


def test_failover_to_fallback(fake):
    p, f = fake(), fake()
    p.responses = [500, 500]
    f.responses = [{"ok": True}]
    resp = client(p, f).chat_json(MSGS, SCHEMA)
    assert resp.model == "m-fallback"


def test_bad_json_content_retries_then_fails_over(fake):
    p, f = fake(), fake()
    p.responses = ["garbage", "garbage"]
    f.responses = [{"ok": True}]
    assert client(p, f).chat_json(MSGS, SCHEMA).model == "m-fallback"


def test_both_hosts_down_raises(fake):
    p, f = fake(), fake()
    p.responses = [500, 500]
    f.responses = [500, 500]
    with pytest.raises(LLMError):
        client(p, f).chat_json(MSGS, SCHEMA)


def test_cloud_model_refused():
    with pytest.raises(ValueError, match="cloud"):
        OllamaClient(
            primary_url="http://x",
            fallback_url="http://y",
            primary_model="qwen3.5:cloud",
            fallback_model="m",
        )
