import socket
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Config  # noqa: E402

FAKE_SERVER = '''#!{python}
"""Fake llama-server: answers /health after a short 'loading' phase."""
import sys, time, json
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
t0 = time.time()
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ready = time.time() - t0 > 0.3
        self.send_response(200 if ready else 503)
        self.end_headers()
        self.wfile.write(json.dumps({{"status": "ok" if ready else "loading"}}).encode())
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    """A self-contained repo root with a fake llama-server and fake GGUF files."""
    fake = tmp_path / "fake-llama-server"
    fake.write_text(FAKE_SERVER.format(python=sys.executable))
    fake.chmod(0o755)
    for name in ("a.gguf", "b.gguf"):
        (tmp_path / name).write_bytes(b"GGUF")
    shared = {
        "servers": {"main": {"host": "127.0.0.1", "port": free_port()},
                    "aux": {"host": "127.0.0.1", "port": free_port()}},
        "roles": {"interpreter": "main", "worker": "aux"},
        "models": {"m-a": {"gguf": "a", "args": ["-c", "8192", "--parallel", "1"]},
                   "m-b": {"gguf": "b", "args": ["-c", "4096"]}},
        "llm": {"temperature": 0.6, "structured_output": "json_schema"},
        "server_control": {"health_timeout_s": 20, "state_dir": ".servers"},
    }
    local = {"llama_server_bin": str(fake),
             "gguf_paths": {"a": str(tmp_path / "a.gguf"), "b": str(tmp_path / "b.gguf")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(shared))
    (tmp_path / "config.local.yaml").write_text(yaml.safe_dump(local))
    return Config.load(tmp_path)
