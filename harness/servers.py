"""llama-server process control: start / stop / switch / status.

State of each managed server (pid, model, command) is kept in
`<state_dir>/<server>.json`, its log in `<state_dir>/<server>.log`.
CLI: python -m harness.servers {start,stop,switch,status} ...  (wrapped by tools/servers.sh)
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from harness.config import Config

FLAGS_MANAGED = {"-m", "--model", "--host", "--port"}


def build_command(
    cfg: Config,
    model: str,
    server: str = "main",
    extra_args: list[str] | None = None,
    args_override: list[str] | None = None,
) -> list[str]:
    """Full llama-server argv for `model` on `server`."""
    m = cfg.model(model)
    args = list(args_override if args_override is not None else m.get("args", []))
    for flag in FLAGS_MANAGED:
        if flag in args:
            raise ValueError(f"'{flag}' is set by the harness; remove it from models.{model}.args")
    s = cfg.server(server)
    return [
        str(cfg.llama_server_bin()),
        "-m", str(cfg.gguf_path(model)),
        "--host", str(s["host"]),
        "--port", str(s["port"]),
        *args,
        *(extra_args or []),
    ]


def _state_dir(cfg: Config) -> Path:
    d = cfg.root / cfg.get("server_control", {}).get("state_dir", ".servers")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file(cfg: Config, server: str) -> Path:
    return _state_dir(cfg) / f"{server}.json"


def _read_state(cfg: Config, server: str) -> dict | None:
    f = _state_file(cfg, server)
    if not f.exists():
        return None
    return json.loads(f.read_text())


def _pid_alive(pid: int) -> bool:
    # If the server is our own child, reap it first: an exited child stays a zombie
    # (and passes kill(pid, 0)) until waited for.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass  # not our child (started by another invocation): fall through
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def health(cfg: Config, server: str, timeout: float = 2.0) -> str:
    """'ok' | 'loading' | 'down'."""
    try:
        r = httpx.get(cfg.server_url(server) + "/health", timeout=timeout)
    except httpx.HTTPError:
        return "down"
    if r.status_code == 200:
        return "ok"
    if r.status_code == 503:
        return "loading"
    return f"http_{r.status_code}"


def wait_healthy(cfg: Config, server: str, proc: subprocess.Popen, timeout_s: float) -> float:
    """Block until /health is ok. Returns seconds waited. Raises if process dies or timeout."""
    t0 = time.monotonic()
    while True:
        if health(cfg, server) == "ok":
            return time.monotonic() - t0
        # poll() reaps the child, so a crashed server is not mistaken for a live zombie
        if proc.poll() is not None:
            log = _state_dir(cfg) / f"{server}.log"
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-30:]) if log.exists() else ""
            raise RuntimeError(f"llama-server '{server}' exited during startup. Log tail:\n{tail}")
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"llama-server '{server}' not healthy after {timeout_s}s")
        time.sleep(0.5)


def start(
    cfg: Config,
    model: str,
    server: str = "main",
    extra_args: list[str] | None = None,
    args_override: list[str] | None = None,
    wait: bool = True,
) -> dict:
    st = status(cfg, server)
    if st["process"] == "running":
        raise RuntimeError(f"server '{server}' already running (pid {st['pid']}, model {st['model']})")
    if health(cfg, server) != "down":
        raise RuntimeError(f"port of server '{server}' is busy with an unmanaged process")
    cmd = build_command(cfg, model, server, extra_args, args_override)
    if not Path(cmd[0]).exists():
        raise FileNotFoundError(f"llama-server binary not found: {cmd[0]}")
    if not Path(cmd[2]).exists():
        raise FileNotFoundError(f"GGUF not found: {cmd[2]}")
    log = open(_state_dir(cfg) / f"{server}.log", "w")
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state = {"pid": proc.pid, "model": model, "server": server, "cmd": cmd, "started_at": time.time()}
    _state_file(cfg, server).write_text(json.dumps(state, indent=2))
    if wait:
        timeout = cfg.get("server_control", {}).get("health_timeout_s", 300)
        wait_healthy(cfg, server, proc, timeout)
        state["startup_s"] = round(time.monotonic() - t0, 2)
    return state


def stop(cfg: Config, server: str = "main", timeout_s: float = 30) -> bool:
    """Stop a managed server. Returns True if a process was stopped."""
    st = _read_state(cfg, server)
    if not st:
        return False
    pid = st["pid"]
    stopped = False
    if _pid_alive(pid):
        os.killpg(pid, signal.SIGTERM)
        t0 = time.monotonic()
        while _pid_alive(pid) and time.monotonic() - t0 < timeout_s:
            time.sleep(0.2)
        if _pid_alive(pid):
            os.killpg(pid, signal.SIGKILL)
        stopped = True
    _state_file(cfg, server).unlink(missing_ok=True)
    return stopped


def switch(cfg: Config, model: str, server: str = "main", extra_args: list[str] | None = None) -> dict:
    t0 = time.monotonic()
    stop(cfg, server)
    state = start(cfg, model, server, extra_args)
    state["switch_s"] = round(time.monotonic() - t0, 2)
    return state


def status(cfg: Config, server: str = "main") -> dict:
    st = _read_state(cfg, server) or {}
    pid = st.get("pid")
    if pid and _pid_alive(pid):
        process = "running"
    elif pid:
        process = "dead"
    else:
        process = "none"
    return {"server": server, "url": cfg.server_url(server), "process": process,
            "pid": pid, "model": st.get("model"), "health": health(cfg, server)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="servers", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("start", "switch"):
        sp = sub.add_parser(name)
        sp.add_argument("model")
        sp.add_argument("--server", default="main")
        sp.add_argument("--extra", default="", help="extra llama-server args, one quoted string")
        sp.add_argument("--dry-run", action="store_true", help="print the command only")
    sp = sub.add_parser("stop")
    sp.add_argument("--server", default="main")
    sp = sub.add_parser("status")
    sp.add_argument("--server", default=None, help="default: all servers")
    a = p.parse_args(argv)
    cfg = Config.load()

    if a.cmd in ("start", "switch"):
        extra = shlex.split(a.extra)
        if a.dry_run:
            print(shlex.join(build_command(cfg, a.model, a.server, extra)))
            return 0
        fn = start if a.cmd == "start" else switch
        print(json.dumps(fn(cfg, a.model, a.server, extra), indent=2))
    elif a.cmd == "stop":
        print("stopped" if stop(cfg, a.server) else "not running")
    else:
        names = [a.server] if a.server else list(cfg["servers"])
        for n in names:
            print(json.dumps(status(cfg, n)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
