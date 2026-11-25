"""Run FIX2 TOTE on one or more task directories with the real model.

  run_task.py bench/toy/toy_off_by_one bench/toy/toy_two_modules --model qwen-9b --seed 1
Starts/switches llama-server to --model if needed (unless --no-server).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import servers  # noqa: E402
from harness.config import Config  # noqa: E402
from harness.llm import LLMClient  # noqa: E402
from harness.skills import SkillLibrary  # noqa: E402
from harness.tote import Tote, ToteConfig  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tasks", nargs="+", type=Path)
    p.add_argument("--model", default="qwen-9b")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--run-id", default=None)
    p.add_argument("--skills", type=Path, default=None, help="skill library dir (default: none)")
    p.add_argument("--no-server", action="store_true", help="use the running server as is")
    p.add_argument("--max-llm-calls", type=int, default=150)
    a = p.parse_args()
    cfg = Config.load()

    if not a.no_server:
        st = servers.status(cfg, "main")
        if st["process"] != "running" or st["model"] != a.model:
            servers.switch(cfg, a.model, "main")
    run_id = a.run_id or time.strftime(f"%Y%m%d-%H%M%S-{a.model}-s{a.seed}")
    interp = LLMClient.for_role(cfg, "interpreter")
    worker = LLMClient.for_role(cfg, "worker")
    tc = ToteConfig(seed=a.seed, max_llm_calls=a.max_llm_calls)
    results = []
    for task in a.tasks:
        t = Tote(task, ROOT / "runs" / run_id, interp, worker, SkillLibrary(a.skills), tc,
                 escalation_dir=ROOT / "escalations" / run_id)
        o = t.run()
        results.append(o.__dict__)
        print(json.dumps({k: o.__dict__[k] for k in
                          ("task_id", "solved", "escalated", "reason", "iterations", "llm_calls",
                           "regressions", "wall_time_s")}, ensure_ascii=False), flush=True)
    print(f"run_id={run_id}  solved {sum(r['solved'] for r in results)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
