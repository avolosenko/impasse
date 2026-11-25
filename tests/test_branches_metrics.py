import csv
import json
import re
from pathlib import Path

from harness.metrics import changed_lines, hypothesis_hit, patch_targets
from harness.results import FIELDS, append_rows, row
from harness.tote import Tote, ToteConfig
from tests.mock_llm import MockLLM, act, hyp, plan

TOY = Path(__file__).resolve().parent.parent / "bench" / "toy"
FIX = dict(path="windows.py", old="range(len(xs) - k)", new="range(len(xs) - k + 1)")
BREAK = dict(path="windows.py", old="out = []", new="out = [0]")


def finish():
    return act("finish", status="done", hypothesis="h", evidence="e", changes="c")


# --- operation-type restrictions ------------------------------------------

def test_read_only_operation_cannot_patch(tmp_path):
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs",
             MockLLM([hyp(), plan("read_more_code"), hyp(), plan("local_fix")]),
             MockLLM([act("apply_patch", **FIX), finish(), act("apply_patch", **FIX), finish()]))
    o = t.run()
    assert o.solved and o.iterations == 2 and o.op_violations == 1
    att = [json.loads(l) for l in (tmp_path / "runs/toy_off_by_one/attempts.jsonl").read_text().splitlines()]
    assert att[0]["op_violations"] == 1 and att[0]["diff"] == ""
    obs = t.worker.calls[1]["messages"][-1]["content"]
    assert "not allowed in this operation" in obs


def test_add_logging_edits_are_reverted(tmp_path):
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs",
             MockLLM([hyp(), plan("add_logging")]), MockLLM([act("apply_patch", **FIX), finish()]),
             cfg=ToteConfig(max_iterations=1))
    o = t.run()
    assert not o.solved and o.reason == "max_iterations"
    assert "range(len(xs) - k)" in (tmp_path / "runs/toy_off_by_one/workspace/windows.py").read_text()


# --- hypothesis_hit ---------------------------------------------------------

def test_changed_lines_and_targets():
    assert changed_lines((TOY / "toy_two_modules" / "solution.patch").read_text()) == {"units.py": {4}}
    pt = patch_targets(TOY / "toy_two_modules")
    assert pt.functions == {"units.py": {"to_minutes"}}
    assert patch_targets(TOY) is None


def test_hypothesis_hit_rules():
    pt = patch_targets(TOY / "toy_two_modules")
    assert hypothesis_hit("units.py:to_minutes", pt)
    assert hypothesis_hit("`units.py:to_minutes()`", pt)
    assert hypothesis_hit("units.py:8", pt)          # within ±5 lines
    assert hypothesis_hit("to_minutes", pt)          # function only
    assert not hypothesis_hit("schedule.py:total_minutes", pt)  # symptom, not cause
    assert not hypothesis_hit("units.py:20", pt)
    assert not hypothesis_hit("schedule.py:to_minutes", pt)     # wrong file
    assert hypothesis_hit("x.py:f", None) is None


def test_hit_recorded_in_outcome(tmp_path):
    t = Tote(TOY / "toy_two_modules", tmp_path / "runs",
             MockLLM([hyp(location="schedule.py:total_minutes"), plan("read_more_code"),
                      hyp(location="units.py:to_minutes"), plan()]),
             MockLLM([finish(), act("apply_patch", path="units.py", old="int(minutes) * 60 + int(hours)",
                                   new="int(hours) * 60 + int(minutes)"), finish()]))
    o = t.run()
    assert o.solved and o.hypothesis_hit is True and o.first_hit_iteration == 2


# --- breadth K ----------------------------------------------------------------

def branch_llm(good_category: str):
    """Interpreter + worker in one: the branch assigned `good_category` fixes the bug,
    every other branch makes a harmless no-op edit or a regression."""
    def reply(messages, kw):
        sys_msg = messages[0]["content"]
        if sys_msg.startswith("You are a debugging interpreter"):
            m = re.search(r"category '(\w+)'", sys_msg)
            cat = m.group(1) if m else "none"
            return hyp(category=cat, claim=f"claim-{cat}")
        if sys_msg.startswith("Choose one operation"):
            return plan("local_fix")
        task = messages[1]["content"]
        if len(messages) > 2:
            return finish()
        if f"claim-{good_category}" in task:
            return act("apply_patch", **FIX)
        if "claim-state_mutation" in task:
            return act("apply_patch", **BREAK)
        return act("read_file", path="windows.py")
    return reply


def test_breadth_assigns_distinct_categories_and_code_selects_winner(tmp_path):
    llm = MockLLM(branch_llm("data_flow"))
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", llm, llm, cfg=ToteConfig(breadth=3, seed=1))
    o = t.run()
    assert o.solved and o.iterations == 1 and o.hypotheses_total == 3
    att = [json.loads(l) for l in (tmp_path / "runs/toy_off_by_one/attempts.jsonl").read_text().splitlines()]
    assert [a["branch_category"] for a in att] == ["boundaries", "state_mutation", "data_flow"]
    assert [a["winner"] for a in att] == [False, False, True]
    assert att[1]["rolled_back"] is True and o.regressions == 1
    assert "range(len(xs) - k + 1)" in (tmp_path / "runs/toy_off_by_one/workspace/windows.py").read_text()
    assert not list((tmp_path / "runs/toy_off_by_one").glob("workspace.b*"))


def test_breadth_rotates_categories_after_failure(tmp_path):
    llm = MockLLM(branch_llm("call_order"))   # 5th category: needs a second iteration with K=3
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", llm, llm, cfg=ToteConfig(breadth=3, seed=1))
    o = t.run()
    assert o.solved and o.iterations == 2
    att = [json.loads(l) for l in (tmp_path / "runs/toy_off_by_one/attempts.jsonl").read_text().splitlines()]
    assert [a["branch_category"] for a in att if a["iter"] == 2] == ["condition_logic", "types_formats", "call_order"]


def test_breadth_seeds_are_deterministic(tmp_path):
    seeds = []
    for run in range(2):
        llm = MockLLM(branch_llm("data_flow"))
        Tote(TOY / "toy_off_by_one", tmp_path / f"r{run}", llm, llm, cfg=ToteConfig(breadth=3, seed=7)).run()
        seeds.append(sorted(c.get("seed") for c in llm.calls))
    assert seeds[0] == seeds[1] and len(set(seeds[0])) == len(seeds[0])


# --- results CSV ----------------------------------------------------------------

def test_results_row_and_csv(tmp_path):
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", MockLLM([hyp(), plan()]),
             MockLLM([act("apply_patch", **FIX), finish()]))
    o = t.run()
    r = row(o.__dict__, run_id="r1", task_dir=TOY / "toy_off_by_one", config="B1", model="qwen-9b", seed=1)
    assert set(r) == set(FIELDS)
    assert (r["solved"], r["hypothesis_hit"], r["first_hit_iteration"], r["orchestration"]) == (1, 1, 1, "FIX2")
    out = tmp_path / "res.csv"
    append_rows(out, [r])
    append_rows(out, [r])
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2 and rows[0]["task_id"] == "toy_off_by_one"


# --- B0 single ReAct agent ------------------------------------------------------

def test_b0_solves_with_one_context(tmp_path):
    from harness.react import run_b0
    llm = MockLLM([act("read_file", path="windows.py"), act("apply_patch", **FIX), act("run_tests"), finish()])
    o = run_b0(TOY / "toy_off_by_one", tmp_path / "runs", llm, seed=1)
    assert o.solved and o.llm_calls == 4 and o.iterations == 4
    assert len(llm.calls[-1]["messages"]) == 8   # one growing context
    assert "Failing tests: 2" in llm.calls[0]["messages"][1]["content"]


def test_b0_budget_and_failure(tmp_path):
    from harness.llm import LLMError
    from harness.react import run_b0
    o = run_b0(TOY / "toy_off_by_one", tmp_path / "runs",
               MockLLM([act("read_file", path="windows.py", start=str(i)) for i in range(1, 20)]),
               max_llm_calls=5, escalation_dir=tmp_path / "esc")
    assert not o.solved and o.reason.startswith(("budget", "step_limit")) and o.llm_calls == 5
    assert (tmp_path / "esc/toy_off_by_one/summary.json").exists()

    class Overflow(MockLLM):
        def chat(self, messages, **kw):
            raise LLMError("HTTP 400: the request exceeds the available context size")
    o = run_b0(TOY / "toy_off_by_one", tmp_path / "runs2", Overflow([]))
    assert o.reason == "context_overflow"


# --- max_depth 3: minimal_repro child TOTE -------------------------------------

REPRO_OK = "from windows import window_sums\nassert window_sums([1, 2, 3, 4], 2) == [3, 5, 7], window_sums([1, 2, 3, 4], 2)"
REPRO_WEAK = "from windows import window_sums\nprint(window_sums([1, 2, 3, 4], 2))"


def test_depth3_repro_child_retries_until_its_test_passes(tmp_path):
    worker = MockLLM([act("run_python", code=REPRO_WEAK), finish(),          # child iter 1: exits 0 -> not a repro
                      act("run_python", code=REPRO_OK), finish(),            # child iter 2: AssertionError -> done
                      act("apply_patch", **FIX), finish()])                  # parent iter 2: fix
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", MockLLM([hyp(), plan("minimal_repro"), hyp(), plan()]),
             worker, cfg=ToteConfig(max_depth=3))
    o = t.run()
    assert o.solved and o.iterations == 2 and o.max_depth_reached == 3
    att = [json.loads(l) for l in (tmp_path / "runs/toy_off_by_one/attempts.jsonl").read_text().splitlines()]
    assert att[0]["result"]["status"] == "done" and "repro reproduces AssertionError" in att[0]["result"]["evidence"]
    assert "did not reproduce AssertionError" in worker.calls[2]["messages"][1]["content"]
    # the parent sees the child's contract in its attempt log, not the child's history
    assert "minimal_repro" in t.interpreter.calls[2]["messages"][1]["content"]


def test_depth3_child_gives_up_and_depth2_has_no_child(tmp_path):
    t = Tote(TOY / "toy_off_by_one", tmp_path / "a", MockLLM([hyp(), plan("minimal_repro")]),
             MockLLM([act("run_python", code=REPRO_WEAK), finish()] * 3),
             cfg=ToteConfig(max_depth=3, max_iterations=1))
    o = t.run()
    att = json.loads((tmp_path / "a/toy_off_by_one/attempts.jsonl").read_text().splitlines()[0])
    assert att["result"]["status"] == "fail" and o.llm_calls == 2 + 6
    t2 = Tote(TOY / "toy_off_by_one", tmp_path / "b", MockLLM([hyp(), plan("minimal_repro")]),
              MockLLM([act("run_python", code=REPRO_WEAK), finish()]), cfg=ToteConfig(max_depth=2, max_iterations=1))
    o2 = t2.run()
    assert o2.max_depth_reached == 2 and o2.llm_calls == 4


def test_depth3_child_with_long_plan_reason(tmp_path):
    """Regression: the child instruction used to be glued into PlanOperation.reason (max 300 chars)."""
    long_plan = {"operation": "minimal_repro", "reason": "r" * 290}
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", MockLLM([hyp(), long_plan]),
             MockLLM([act("run_python", code=REPRO_OK), finish()]), cfg=ToteConfig(max_depth=3, max_iterations=1))
    o = t.run()
    assert o.max_depth_reached == 3 and o.reason == "max_iterations"


def test_attempt_log_shows_stage_results_to_the_model(tmp_path):
    """Regression: the planner must see that a diagnosis already succeeded."""
    worker = MockLLM([act("finish", status="done", evidence="windows.py:6 range stops one early"),
                      act("apply_patch", **FIX), finish()])
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs",
             MockLLM([hyp(), plan("read_more_code"), hyp(), plan()]), worker)
    assert t.run().solved
    second_plan_prompt = t.interpreter.calls[3]["messages"][1]["content"]
    assert "result: done — windows.py:6 range stops one early" in second_plan_prompt


# --- detector policy: strict (SPEC 3.6 literally) vs lenient -------------------

DIAG = [act("read_file", path="windows.py"), finish()]


def test_strict_counts_diagnostic_iterations(tmp_path):
    t = Tote(TOY / "toy_off_by_one", tmp_path / "r", MockLLM([hyp(), plan("read_more_code")] * 4),
             MockLLM(DIAG * 4), cfg=ToteConfig(detector_policy="strict"))
    o = t.run()
    assert o.escalated and o.iterations == 3 and "same_error_x3" in o.reason


def test_lenient_ignores_diagnostic_iterations(tmp_path):
    t = Tote(TOY / "toy_off_by_one", tmp_path / "r",
             MockLLM([hyp(), plan("read_more_code")] * 4 + [hyp(), plan()]),
             MockLLM(DIAG * 4 + [act("apply_patch", **FIX), finish()]), cfg=ToteConfig(detector_policy="lenient"))
    o = t.run()
    assert o.solved and o.iterations == 5 and o.detector_fires == {}


def test_lenient_gives_one_retry_then_escalates(tmp_path):
    # useless (but different) edits: same error on edit-iterations 1-3 -> fire, retry granted;
    # iteration 4 -> fires again -> escalate
    worker = []
    for i in range(6):
        worker += [act("apply_patch", path="windows.py", old="    return out",
                       new="    return out" + " " * (i + 1)), finish()]
    t = Tote(TOY / "toy_off_by_one", tmp_path / "r", MockLLM([hyp(), plan()] * 6), MockLLM(worker),
             cfg=ToteConfig(detector_policy="lenient"))
    o = t.run()
    assert o.escalated and o.iterations == 4 and o.detector_fires["same_error_x3"] == 2


def test_lenient_step_limit_retry(tmp_path):
    loop = [act("read_file", path="windows.py", start=str(i)) for i in range(1, 9)]
    for policy, iters in (("strict", 1), ("lenient", 2)):
        t = Tote(TOY / "toy_off_by_one", tmp_path / policy, MockLLM([hyp(), plan("read_more_code")] * 3),
                 MockLLM(loop * 3), cfg=ToteConfig(detector_policy=policy))
        o = t.run()
        assert o.escalated and o.iterations == iters and "step_limit" in o.reason, policy


def test_bad_policy_rejected(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        Tote(TOY / "toy_off_by_one", tmp_path / "r", MockLLM([]), MockLLM([]), cfg=ToteConfig(detector_policy="x"))
