"""Stage-1 smoke test for llama-server.

  smoke.py run   [--server main] [--tag TAG]   # test an already running server
  smoke.py suite [--models qwen-4b qwen-9b] [--kv-q8] [--mixed]
                                               # start/switch servers itself, run all checks

Checks: health, per-slot context, structured output (each constraint mode),
thinking switch, decode/prompt speed at 1 and N parallel requests, VRAM, model
switch time. Results go to reports/stage-1/smoke-<model>[-<tag>].json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import servers  # noqa: E402
from harness.config import Config  # noqa: E402
from harness.llm import LLMClient, LLMError, StructuredOutputError  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "stage-1"
MIN_SLOT_CTX = 8192
MIN_FREE_VRAM_MB = 1024


# Subagent action protocol (SPEC 3.3), used as the structured-output probe.
class Action(BaseModel):
    action: Literal["read_file", "search", "apply_patch", "run_tests", "run_python", "finish"]
    args: dict[str, str]
    thought: str = Field(max_length=300)


ACTION_GBNF = r'''
root   ::= "{" ws "\"action\"" ws ":" ws act ws "," ws "\"args\"" ws ":" ws obj ws "," ws "\"thought\"" ws ":" ws str ws "}"
act    ::= "\"read_file\"" | "\"search\"" | "\"apply_patch\"" | "\"run_tests\"" | "\"run_python\"" | "\"finish\""
obj    ::= "{" ws ( kv ( ws "," ws kv )* )? ws "}"
kv     ::= str ws ":" ws str
str    ::= "\"" ( [^"\\\x00-\x1f] | "\\" ["\\/bfnrt] )* "\""
ws     ::= [ \t\n]{0,4}
'''

PROBE_MESSAGES = [
    {"role": "system", "content": (
        "You are a debugging agent. Reply with exactly one JSON action: "
        '{"action": ..., "args": {...}, "thought": "one short sentence"}. '
        "Allowed actions: read_file, search, apply_patch, run_tests, run_python, finish.")},
    {"role": "user", "content": (
        "pytest output:\nFAILED tests/test_stack.py::test_pop_empty - IndexError: pop from empty list\n"
        "  File \"src/stack.py\", line 14, in pop\n    return self.items.pop()\n"
        "Choose the next action.")},
]

LONG_TEXT_UNIT = (
    "The quick brown fox jumps over the lazy dog while the debugger steps through "
    "each frame of the stack and records every variable it sees. "
)


def nvidia_smi() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        total, used, free = (int(x) for x in out.splitlines()[0].split(","))
        return {"total_mb": total, "used_mb": used, "free_mb": free}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def server_info(client: LLMClient) -> dict:
    p = client.props()
    gen = p.get("default_generation_settings", {})
    n_ctx_slot = gen.get("n_ctx")
    slots = p.get("total_slots")
    return {
        # basename only: absolute paths are machine-specific and must not be committed
        "model_file": Path(p.get("model_path") or "").name or None,
        "build_info": p.get("build_info"),
        "total_slots": slots,
        "n_ctx_per_slot": n_ctx_slot,
        "slot_ctx_ok": bool(n_ctx_slot and n_ctx_slot >= MIN_SLOT_CTX),
    }


def check_structured(client: LLMClient, trials: int = 3) -> dict:
    results = {}
    for mode in ("json_schema", "json_object", "grammar"):
        ok, errors, samples = 0, [], []
        for i in range(trials):
            try:
                if mode == "grammar":
                    r = client.chat(PROBE_MESSAGES, grammar=ACTION_GBNF, seed=i, max_tokens=300)
                    Action.model_validate(r.parsed)
                else:
                    r = client.chat(PROBE_MESSAGES, schema=Action, mode=mode, seed=i, max_tokens=300)
                ok += 1
                samples.append(r.text[:200])
            except (StructuredOutputError, LLMError, ValueError) as e:
                errors.append(str(e)[:300])
        results[mode] = {"valid": ok, "trials": trials, "errors": errors[:3], "samples": samples[:2]}
    return results


def check_thinking(client: LLMClient) -> dict:
    """With enable_thinking=false there must be no reasoning text."""
    r = client.chat([{"role": "user", "content": "What is 17*23? Answer with the number only."}],
                    max_tokens=200, seed=0)
    leaked = bool(r.reasoning) or "<think>" in r.text
    return {"enable_thinking": client.enable_thinking, "reasoning_present": leaked,
            "answer": r.text.strip()[:100], "completion_tokens": r.completion_tokens}


def _gen_request(client: LLMClient, n_tokens: int, seed: int):
    msgs = [{"role": "user", "content": "Write a long, detailed essay about debugging software."}]
    return client.chat(msgs, max_tokens=n_tokens, seed=seed, extra={"ignore_eos": True})


def check_throughput(client: LLMClient, parallel: int, n_tokens: int = 256) -> dict:
    _gen_request(client, 16, 0)  # warm-up
    out = {}
    for n in sorted({1, parallel}):
        t0 = time.monotonic()
        with ThreadPoolExecutor(n) as ex:
            res = list(ex.map(lambda s: _gen_request(client, n_tokens, s), range(n)))
        wall = time.monotonic() - t0
        total = sum(r.completion_tokens for r in res)
        out[f"n{n}"] = {
            "requests": n,
            "wall_s": round(wall, 2),
            "aggregate_tok_s": round(total / wall, 1),
            "per_request_tok_s": round(sum(r.tokens_per_s for r in res) / n, 1),
        }
    return out


def check_prompt_processing(client: LLMClient, approx_tokens: int = 6000) -> dict:
    """One long prompt close to the per-slot budget: prompt speed and that it fits."""
    text = LONG_TEXT_UNIT * (approx_tokens // 28)
    msgs = [{"role": "user", "content": text + "\n\nSummarize the text above in one sentence."}]
    try:
        r = client.chat(msgs, max_tokens=32, seed=0, extra={"cache_prompt": False})
    except LLMError as e:
        return {"ok": False, "error": str(e)[:300]}
    timings = r.raw.get("timings", {})
    return {"ok": True, "prompt_tokens": r.prompt_tokens,
            "prompt_tok_s": round(timings.get("prompt_per_second", 0), 1),
            "latency_s": round(r.latency_s, 2)}


def run_checks(cfg: Config, server: str = "main", model: str | None = None) -> dict:
    client = LLMClient(cfg.server_url(server), temperature=cfg["llm"]["temperature"],
                       enable_thinking=False, timeout_s=600)
    try:
        info = server_info(client)
        parallel = info["total_slots"] or 1
        report = {
            "server": server,
            "model": model or servers.status(cfg, server).get("model"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "server_info": info,
            "vram_after_load": nvidia_smi(),
            "structured_output": check_structured(client),
            "thinking_off": check_thinking(client),
            "throughput": check_throughput(client, parallel),
            "long_prompt": check_prompt_processing(client),
        }
        report["vram_after_load"]["free_ok"] = (
            report["vram_after_load"].get("free_mb", 0) >= MIN_FREE_VRAM_MB)
        return report
    finally:
        client.close()


def save(report: dict, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{name}.json"
    p.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return p


def suite(cfg: Config, models: list[str], kv_q8: bool, mixed: bool) -> None:
    for srv in cfg["servers"]:
        servers.stop(cfg, srv)
    time.sleep(2)
    baseline = nvidia_smi()
    print("VRAM baseline (no server):", baseline)
    for model in models:
        st = servers.switch(cfg, model, "main")
        rep = run_checks(cfg, "main", model)
        rep["vram_baseline"] = baseline
        rep["switch_s"] = st.get("switch_s")
        rep["startup_s"] = st.get("startup_s")
        rep["cmd"] = st["cmd"][3:]  # drop binary and model path (machine-specific)
        print("saved", save(rep, f"smoke-{model}"))
        if kv_q8:
            st = servers.switch(cfg, model, "main", ["-ctk", "q8_0", "-ctv", "q8_0"])
            rep = run_checks(cfg, "main", model)
            rep["vram_baseline"] = baseline
            rep["kv_cache"] = "q8_0"
            print("saved", save(rep, f"smoke-{model}-kvq8"))
    servers.stop(cfg, "main")
    if mixed:
        print("saved", save(mixed_attempt(cfg, baseline), "smoke-mixed"))


def mixed_attempt(cfg: Config, baseline: dict) -> dict:
    mx = cfg["mixed"]
    rep: dict = {"vram_baseline": baseline, "fits": False}
    try:
        for srv in ("main", "aux"):
            st = servers.start(cfg, mx[srv]["model"], srv, args_override=mx[srv]["args"])
            rep[f"{srv}_startup_s"] = st.get("startup_s")
        rep["vram_after_load"] = nvidia_smi()
        for srv in ("main", "aux"):
            rep[srv] = run_checks(cfg, srv, mx[srv]["model"])
        # Both servers under load at once.
        clients = [LLMClient(cfg.server_url(s), enable_thinking=False) for s in ("main", "aux")]
        t0 = time.monotonic()
        with ThreadPoolExecutor(2) as ex:
            res = list(ex.map(lambda c: _gen_request(c, 256, 0), clients))
        rep["concurrent"] = {"wall_s": round(time.monotonic() - t0, 2),
                             "per_server_tok_s": [round(r.tokens_per_s, 1) for r in res]}
        rep["vram_under_load"] = nvidia_smi()
        free = rep["vram_under_load"].get("free_mb", 0)
        rep["fits"] = (free >= MIN_FREE_VRAM_MB and
                       all(rep[s]["server_info"]["slot_ctx_ok"] for s in ("main", "aux")))
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"{type(e).__name__}: {e}"[:1000]
    finally:
        for srv in ("main", "aux"):
            servers.stop(cfg, srv)
    return rep


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--server", default="main")
    r.add_argument("--tag", default="")
    s = sub.add_parser("suite")
    s.add_argument("--models", nargs="+", default=["qwen-4b", "qwen-9b"])
    s.add_argument("--kv-q8", action="store_true", help="also test q8_0 KV cache")
    s.add_argument("--mixed", action="store_true", help="also try two servers on one GPU")
    a = p.parse_args()
    cfg = Config.load()
    if a.cmd == "run":
        rep = run_checks(cfg, a.server)
        name = f"smoke-{rep['model'] or a.server}" + (f"-{a.tag}" if a.tag else "")
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        print("saved", save(rep, name))
    else:
        suite(cfg, a.models, a.kv_q8, a.mixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
