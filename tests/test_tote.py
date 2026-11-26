import json
from pathlib import Path

from harness import detectors as D
from harness.skills import SkillLibrary
from harness.subagent import Budget, Transcript, run_subagent
from harness.tote import Tote, ToteConfig
from harness.workspace import Workspace
from tests.mock_llm import MockLLM, act, hyp, plan

TOY = Path(__file__).resolve().parent.parent / "bench" / "toy"
FIX = dict(path="windows.py", old="range(len(xs) - k)", new="range(len(xs) - k + 1)")
BREAK = dict(path="windows.py", old="out = []", new="out = [0]")


def finish(**kw):
    return act("finish", status="done", hypothesis="h", evidence="windows.py:6", changes="fixed", **kw)


def make(tmp_path, interp, worker, **cfg):
    return Tote(TOY / "toy_off_by_one", tmp_path / "runs", MockLLM(interp), MockLLM(worker),
                cfg=ToteConfig(**cfg), escalation_dir=tmp_path / "esc")


def test_solves_in_one_iteration(tmp_path):
    t = make(tmp_path, [hyp(), plan()],
             [act("read_file", path="windows.py"), act("apply_patch", **FIX), act("run_tests"), finish()])
    o = t.run()
    assert o.solved and not o.escalated and o.iterations == 1
    assert o.llm_calls == 6 and o.hypotheses_total == 1
    d = tmp_path / "runs" / "toy_off_by_one"
    for f in ("goal.md", "state.md", "attempts.jsonl", "result.json", "final.diff", "transcript/calls.jsonl"):
        assert (d / f).exists(), f
    att = [json.loads(l) for l in (d / "attempts.jsonl").read_text().splitlines()]
    assert att[0]["failing_before"] == 2 and att[0]["failing_after"] == 0
    assert "+    for i in range(len(xs) - k + 1):" in att[0]["diff"]
    assert json.loads((d / "result.json").read_text())["solved"] is True
    # the original task directory is untouched
    assert "range(len(xs) - k)" in (TOY / "toy_off_by_one" / "windows.py").read_text()


def test_regression_is_rolled_back(tmp_path):
    t = make(tmp_path, [hyp(), plan(), hyp(), plan()],
             [act("apply_patch", **BREAK), finish(), act("apply_patch", **FIX), finish()])
    o = t.run()
    assert o.solved and o.regressions == 1 and o.iterations == 2
    att = [json.loads(l) for l in (tmp_path / "runs/toy_off_by_one/attempts.jsonl").read_text().splitlines()]
    assert att[0]["rolled_back"] is True and att[0]["failing_after"] == 3
    assert "+    out = [0]" in att[0]["diff"]


def test_tamper_attempt_rejected_and_counted(tmp_path):
    t = make(tmp_path, [hyp(), plan()],
             [act("apply_patch", path="tests/test_windows.py", old="def test_basic", new="def _x"),
              act("apply_patch", **FIX), finish()])
    o = t.run()
    assert o.solved and o.tests_tampering_attempts == 1


def test_same_error_loop_escalates_with_package(tmp_path):
    noop = [act("read_file", path="windows.py"), finish()]
    t = make(tmp_path, [hyp(), plan()] * 5, noop * 5)
    o = t.run()
    assert o.escalated and not o.solved and o.iterations == 3
    assert D.SAME_ERROR in o.reason and o.detector_fires[D.SAME_ERROR] == 1
    esc = tmp_path / "esc" / "toy_off_by_one"
    s = json.loads((esc / "summary.json").read_text())
    assert s["detectors"] == [D.SAME_ERROR] and s["iterations"] == 3
    assert (esc / "attempts.jsonl").exists() and (esc / "state.md").exists()


def test_detector_loads_skill_before_escalating(tmp_path):
    skills = tmp_path / "skills" / "same-error"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: same-error\nphase: operate\ntriggers: [same_error_x3]\n---\nDo X.\n")
    noop = [act("read_file", path="windows.py"), finish()]
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", MockLLM([hyp(), plan()] * 6), MockLLM(noop * 6),
             skills=SkillLibrary(tmp_path / "skills"), escalation_dir=tmp_path / "esc")
    o = t.run()
    # 3rd same error -> skill loaded (no escalation); 4th -> skill did not help -> escalate
    assert o.escalated and o.iterations == 4 and o.skills_loaded == ["same-error"]
    assert o.detector_fires[D.SAME_ERROR] == 2
    # iterations 1-3 run without the skill, iteration 4 (after the fire) with it
    assert "Skill: same-error" not in t.worker.calls[0]["messages"][0]["content"]
    assert "Skill: same-error" in t.worker.calls[-1]["messages"][0]["content"]


def test_budget_exhaustion_escalates(tmp_path):
    t = make(tmp_path, [hyp(), plan()] * 10, [act("read_file", path="windows.py", start=str(i)) for i in range(1, 50)],
             max_llm_calls=7, subagent_steps=8)
    o = t.run()
    assert o.escalated and o.reason.startswith("budget") and o.llm_calls == 7


def test_invalid_hypothesis_without_prediction_is_rejected(tmp_path):
    bad = {"category": "x", "location": "windows.py:f", "claim": "something wrong", "prediction": ""}
    t = make(tmp_path, [bad, hyp(), plan()], [act("apply_patch", **FIX), finish()])
    o = t.run()
    assert o.solved and o.iterations == 2 and o.hypotheses_total == 1


def test_subagent_step_limit_and_repeat_action(tmp_path):
    ws = Workspace.create(TOY / "toy_off_by_one", tmp_path / "ws")
    llm = MockLLM([act("read_file", path="windows.py")] * 3)
    out = run_subagent(llm, ws, "task", [], Budget(), Transcript(None), max_steps=3)
    assert out.result is None and D.STEP_LIMIT in out.fired and D.REPEAT_ACTION in out.fired
    assert "already did exactly this" in llm.calls[-1]["messages"][-1]["content"]


def test_subagent_recovers_from_bad_args_and_bad_json(tmp_path):
    ws = Workspace.create(TOY / "toy_off_by_one", tmp_path / "ws")
    llm = MockLLM(["not json", act("read_file"), act("apply_patch", **FIX), finish()])
    out = run_subagent(llm, ws, "task", [], Budget(), Transcript(None), max_steps=6)
    assert out.result.status == "done" and out.patched
    assert "Invalid action JSON" in llm.calls[1]["messages"][-1]["content"]
    assert "REJECTED: bad args" in llm.calls[2]["messages"][-1]["content"]
