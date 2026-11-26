"""B0 baseline: a single ReAct agent (SPEC 6).

One model, one growing context, the same actions and sandbox as the TOTE workers, the same
LLM-call budget; no stages, no hypotheses, no skills, no rollback. If the context overflows
the server slot, the run ends with reason `context_overflow` (a property of the baseline).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from harness.llm import LLMError
from harness.subagent import Budget, BudgetExhausted, ChatClient, Transcript, run_subagent
from harness.tote import TaskOutcome, _files_of
from harness.workspace import Workspace

B0_TASK = """Goal: {goal}

Fix the bug(s) in this project so that all tests pass. Files in tests/ are read-only.
Source files: {files}

Current test result:
Failing tests: {failing} (passed {passed}). First failure: {first}
{traceback}

Work step by step with the actions. When all tests pass, call finish."""


def run_b0(task_dir: Path, run_dir: Path, client: ChatClient, max_llm_calls: int = 150,
           seed: int | None = None, escalation_dir: Path | None = None) -> TaskOutcome:
    t0 = time.monotonic()
    task_dir = Path(task_dir)
    d = Path(run_dir) / task_dir.name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    ws = Workspace.create(task_dir, d / "workspace")
    tests_sum = ws.tests_checksum()
    goal_p = task_dir / "task.md"
    goal = goal_p.read_text().strip() if goal_p.exists() else "Make all tests pass."
    t = ws.run_tests()
    budget = Budget(max_llm_calls)
    tr = Transcript(d / "transcript" / "calls.jsonl")
    reason, out = "", None
    try:
        out = run_subagent(client, ws, B0_TASK.format(
            goal=goal, files=", ".join(ws.source_files()), failing=t.n_failing, passed=t.passed,
            first=t.first_failure, traceback=t.traceback), [], budget, tr,
            max_steps=max_llm_calls, seed=seed, stage="b0")
        reason = "finished" if out.result else "step_limit"
    except BudgetExhausted as e:
        reason = f"budget: {e}"
    except LLMError as e:
        reason = "context_overflow" if "context" in str(e).lower() else f"llm_error: {e}"
    final = ws.run_tests()
    solved = final.green and ws.tests_checksum() == tests_sum
    if solved:
        reason = "all tests green"
    o = TaskOutcome(task_id=task_dir.name, solved=solved, escalated=not solved, reason=reason,
                    iterations=out.steps if out else 0, llm_calls=budget.llm_calls,
                    prompt_tokens=budget.prompt_tokens, completion_tokens=budget.completion_tokens,
                    wall_time_s=round(time.monotonic() - t0, 2),
                    detector_fires={f: 1 for f in (out.fired if out else [])},
                    tests_tampering_attempts=out.tamper_attempts if out else 0)
    final_diff = Workspace.diff(_files_of(task_dir), ws.source_files())
    (d / "final.diff").write_text(final_diff)
    (d / "result.json").write_text(json.dumps(o.__dict__, indent=2))
    if o.escalated and escalation_dir:
        e = Path(escalation_dir) / task_dir.name
        e.mkdir(parents=True, exist_ok=True)
        (e / "final.diff").write_text(final_diff)
        (e / "summary.json").write_text(json.dumps({"task_id": o.task_id, "reason": reason, "config": "B0",
                                                    "llm_calls": o.llm_calls}, indent=2))
    return o
