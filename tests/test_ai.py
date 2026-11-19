"""AI provider tests: protocol handling against local fake servers, plus a
live test against the real Ollama install when it's available."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from nyxo.ai import credentials
from nyxo.ai.base import AIUnavailable, ChatMessage
from nyxo.ai.cloud import AnthropicProvider, OpenAICompatProvider, _merge_alternating
from nyxo.ai.ollama import OllamaProvider
from nyxo.assistant.assistant import extract_action


class _Fake(BaseHTTPRequestHandler):
    body: bytes = b""
    ctype = "text/event-stream"
    seen: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        type(self).seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(self.rfile.read(n))))
        self.send_response(200)
        self.send_header("Content-Type", self.ctype)
        self.end_headers()
        self.wfile.write(type(self).body)

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"version": "x", "models": [{"name": "tiny:1b"}]}).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    _Fake.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, _Fake
    srv.shutdown()


def test_ollama_protocol(fake_server):
    srv, h = fake_server
    h.ctype = "application/x-ndjson"
    h.body = b"".join(json.dumps(x).encode() + b"\n" for x in [
        {"message": {"content": "Hel"}, "done": False},
        {"message": {"content": "lo!"}, "done": False},
        {"message": {"content": ""}, "done": True}])
    p = OllamaProvider(f"http://127.0.0.1:{srv.server_port}", "tiny:1b", autostart=False)
    toks = []
    assert p.status()[0]
    assert p.chat([ChatMessage("user", "hi")], "sys", on_token=toks.append) == "Hello!"
    assert toks == ["Hel", "lo!"]
    path, _, payload = h.seen[0]
    assert path == "/api/chat" and payload["messages"][0] == {"role": "system", "content": "sys"}


def test_ollama_unreachable():
    p = OllamaProvider("http://127.0.0.1:9", "x", autostart=False)
    assert not p.reachable()
    with pytest.raises(AIUnavailable):
        p.chat([ChatMessage("user", "hi")], "s")


def test_anthropic_protocol(fake_server, monkeypatch):
    srv, h = fake_server
    h.body = (b'event: message_start\ndata: {"type":"message_start"}\n\n'
              b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi "}}\n\n'
              b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"there"}}\n\n'
              b'data: {"type":"message_stop"}\n\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    p = AnthropicProvider("claude-haiku-5-5")
    p.URL = f"http://127.0.0.1:{srv.server_port}/v1/messages"
    out = p.chat([ChatMessage("assistant", "x"), ChatMessage("user", "a"), ChatMessage("user", "b")], "sys")
    assert out == "Hi there"
    _, headers, payload = h.seen[0]
    assert headers["x-api-key"] == "sk-ant-test" and headers["anthropic-version"] == "2023-06-01"
    assert payload["system"] == "sys" and payload["model"] == "claude-haiku-5-5"
    assert payload["messages"] == [{"role": "user", "content": "a\n\nb"}]


def test_anthropic_without_key():
    ok, detail = AnthropicProvider("claude-haiku-5-5").status()
    assert not ok and "API key" in detail
    with pytest.raises(AIUnavailable):
        AnthropicProvider("m").chat([ChatMessage("user", "x")], "s")


def test_openai_protocol(fake_server, monkeypatch):
    srv, h = fake_server
    h.body = (b'data: {"choices":[{"delta":{"content":"Yo"}}]}\n\n'
              b'data: {"choices":[{"delta":{"content":"!"}}]}\n\ndata: [DONE]\n\n')
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123456789012345")
    p = OpenAICompatProvider(f"http://127.0.0.1:{srv.server_port}/v1", "m")
    assert p.chat([ChatMessage("user", "hi")], "sys") == "Yo!"
    path, headers, _ = h.seen[0]
    assert path == "/v1/chat/completions" and headers["authorization"].startswith("Bearer ")


def test_credentials_file(monkeypatch):
    credentials.set_key("ANTHROPIC_API_KEY", "sk-ant-file-key")
    assert credentials.get("ANTHROPIC_API_KEY") == "sk-ant-file-key"
    assert credentials.source("ANTHROPIC_API_KEY") == "credentials file"
    from nyxo.core import paths
    assert (paths.credentials_path().stat().st_mode & 0o777) == 0o600
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-wins")
    assert credentials.get("ANTHROPIC_API_KEY") == "env-wins"
    with pytest.raises(ValueError):
        credentials.set_key("SOMETHING_ELSE", "x")


def test_merge_alternating():
    msgs = [ChatMessage("assistant", "a"), ChatMessage("user", "u1"), ChatMessage("user", "u2"),
            ChatMessage("assistant", "b")]
    assert _merge_alternating(msgs) == [{"role": "user", "content": "u1\n\nu2"},
                                        {"role": "assistant", "content": "b"}]


def test_extract_action():
    vis, act = extract_action('Sure!\nACTION: {"name": "open_app", "args": {"app": "firefox"}}')
    assert vis == "Sure!" and act == {"name": "open_app", "args": {"app": "firefox"}}
    vis, act = extract_action("Just text")
    assert act is None and vis == "Just text"
    vis, act = extract_action("x\nACTION: {broken json")
    assert act is None


LIVE = OllamaProvider("http://127.0.0.1:11434", "qwen2.5:1.5b", autostart=True)


@pytest.mark.skipif(not LIVE.installed(), reason="Ollama not installed")
def test_live_ollama():
    if not LIVE.ensure_server():
        pytest.skip("ollama server could not start")
    if "qwen2.5:1.5b" not in LIVE.models():
        pytest.skip("qwen2.5:1.5b not pulled")
    out = LIVE.chat([ChatMessage("user", "What is the capital of France? Answer in one word.")],
                    "You are a concise assistant.")
    assert "paris" in out.lower()
