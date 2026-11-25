"""Rough wall-time estimate of the full run matrix from stage-1 smoke measurements.

Inputs: reports/stage-1/smoke-<model>.json (decode speed at N parallel, prompt speed).
Assumptions about the workload are CLI flags; print them with the result so the
estimate is reproducible. This is an order-of-magnitude planning tool, not a promise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SMOKE = Path(__file__).resolve().parent.parent / "reports" / "stage-1"


def per_task_seconds(smoke: dict, calls: int, prompt_tok: int, completion_tok: int,
                     cache_hit: float) -> float:
    """Time for one task, assuming its LLM calls are sequential on one slot while
    the other slots serve other tasks (so per-request speed at N parallel applies)."""
    slots = smoke["server_info"]["total_slots"] or 1
    tp = smoke["throughput"][f"n{slots}"]
    decode = tp["per_request_tok_s"]
    # Prompt processing is shared by all slots: per-slot share is total / slots.
    pp = (smoke["long_prompt"].get("prompt_tok_s") or 500) / slots
    per_call = prompt_tok * (1 - cache_hit) / pp + completion_tok / decode
    return calls * per_call


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--models", nargs="+", default=["qwen-4b", "qwen-9b"])
    p.add_argument("--tasks", type=int, default=40)
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--configs", type=int, default=14,
                   help="(config) runs per task per model, after restricting T-D3 etc. to hard tasks")
    p.add_argument("--calls", type=int, default=40, help="avg LLM calls per task (budget cap 150)")
    p.add_argument("--prompt-tokens", type=int, default=3000)
    p.add_argument("--completion-tokens", type=int, default=250)
    p.add_argument("--cache-hit", type=float, default=0.3,
                   help="fraction of prompt tokens served from the KV prompt cache")
    a = p.parse_args()

    total_h = 0.0
    for m in a.models:
        f = SMOKE / f"smoke-{m}.json"
        if not f.exists():
            print(f"{m}: no {f.name}, skipped")
            continue
        smoke = json.loads(f.read_text())
        slots = smoke["server_info"]["total_slots"] or 1
        t_task = per_task_seconds(smoke, a.calls, a.prompt_tokens, a.completion_tokens, a.cache_hit)
        runs = a.tasks * a.seeds * a.configs
        hours = runs * t_task / slots / 3600
        total_h += hours
        print(f"{m}: {t_task/60:.1f} min/task, {slots} slots, {runs} runs -> {hours:.1f} h")
    print(f"TOTAL ≈ {total_h:.1f} h  (assumptions: {vars(a)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
