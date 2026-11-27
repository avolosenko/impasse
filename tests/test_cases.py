import json
from pathlib import Path

import pytest

from harness import cases as C
from harness.contracts import TestRun as Run
from harness.tote import Tote, ToteConfig
from tests.mock_llm import MockLLM, act, hyp, plan

TOY = Path(__file__).resolve().parent.parent / "bench" / "toy"
FIX = dict(path="windows.py", old="range(len(xs) - k)", new="range(len(xs) - k + 1)")
BREAK = dict(path="windows.py", old="out = []", new="out = [0]")


def finish():
    return act("finish", status="done", hypothesis="h", evidence="windows.py:6", changes="fixed")


def _tr(tb, n=2, msg=None):
    from harness.workspace import normalize_traceback
    return Run(failed=n, passed=3, traceback=tb, signature=normalize_traceback(tb), first_message=msg)


TB_A = ('File "/x/y/ledger.py", line 12, in total_amount\n    return sum(row_values[i] for i in range(n))\n'
        "E   IndexError: list index out of range")
TB_B = ('File "/q/report.py", line 40, in build_rows\n    return sum(cells[j] for j in range(m))\n'
        "E   IndexError: list index out of range")
TB_C = 'File "/q/t.py", line 3, in test_x\n    assert parse_time("1:05") == 65\nE   assert 64 == 65'


def test_signature_drops_names_paths_literals():
    s = C.signature(_tr(TB_A))
    assert s["exc_type"] == "IndexError" and s["kind"] == "exception" and s["n_failing"] == 2
    text = json.dumps(s)
    for leak in ("ledger", "total_amount", "row_values", "/x/y", "12"):
        assert leak not in text
    assert C.signature(_tr(TB_C))["kind"] == "wrong_value"


def test_similarity_prefers_same_shape():
    a, b, c = (C.signature(_tr(t)) for t in (TB_A, TB_B, TB_C))
    assert C.similarity(a, a) == 1.0
    assert C.similarity(a, b) > 0.9 > C.similarity(a, c)


def _case(task, sig, steps=5, cid=None, cat="boundaries"):
    return {"case_id": cid or task, "source_task": task, "signature": sig, "steps": steps,
            "hit_hypothesis": {"category": cat, "claim_abstract": "A loop over a range stops one item early."},
            "operation_chain": ["local_fix"]}


def test_retrieve_one_per_task_and_exclusion(tmp_path):
    p = tmp_path / "m.jsonl"
    mem = C.CaseMemory(p)
    a, c = C.signature(_tr(TB_A)), C.signature(_tr(TB_C))
    for case in (_case("t1", a, 7, "c1"), _case("t1", a, 3, "c2"), _case("t2", c, 1, "c3")):
        mem.add(case)
    mem = C.CaseMemory(p)
    got = mem.retrieve(a, m=3)
    assert [x[1]["case_id"] for x in got] == ["c2", "c3"]   # one per task, fewer steps wins a tie
    assert [x[1]["case_id"] for x in mem.retrieve(a, exclude={"c2"})][0] == "c1"
    assert mem.version().startswith("m@")
    assert C.CaseMemory(None).version() == "none"


def _memory_with_case(tmp_path, cat="boundaries"):
    """A memory whose case matches the toy task's initial failure exactly."""
    from harness.workspace import Workspace
    ws = Workspace.create(TOY / "toy_off_by_one", tmp_path / "probe")
    t = ws.run_tests()
    p = tmp_path / "mem.jsonl"
    C.CaseMemory(p).add(_case("other_task", C.signature(t, list(ws.source_files())), cat=cat))
    return C.CaseMemory(p)


def test_memory_on_puts_case_in_hypothesis_prompt_and_labels_source(tmp_path):
    mem = _memory_with_case(tmp_path)
    interp = MockLLM([hyp(), plan()])
    t = Tote(TOY / "toy_off_by_one", tmp_path / "runs", interp,
             MockLLM([act("apply_patch", **FIX), act("run_tests"), finish()]),
             cfg=ToteConfig(memory_mode="on"), memory=mem)
    o = t.run()
    assert o.solved and o.cases_retrieved == 1 and o.hit_hypothesis_source == "cases"
    sys_prompt = interp.calls[0]["messages"][0]["content"]
    assert "past case with a similar symptom" in sys_prompt and "category 'boundaries'" in sys_prompt
    att = json.loads((tmp_path / "runs" / "toy_off_by_one" / "attempts.jsonl").read_text().splitlines()[0])
    assert att["branch_source"] == "cases" and att["hypothesis_source"] == "cases" and att["case_id"] == "other_task"


def test_rejected_case_not_offered_again(tmp_path):
    mem = _memory_with_case(tmp_path)
    interp = MockLLM([hyp(), plan(), hyp(location="windows.py:other", claim="something else entirely"), plan()])
    worker = MockLLM([act("apply_patch", **BREAK), finish(), act("apply_patch", **FIX), finish()])
    o = Tote(TOY / "toy_off_by_one", tmp_path / "runs", interp, worker,
             cfg=ToteConfig(memory_mode="on"), memory=mem).run()
    assert o.solved and o.iterations == 2 and o.cases_retrieved == 1
    assert "past case" not in interp.calls[2]["messages"][0]["content"]


def test_memory_off_and_threshold(tmp_path):
    mem = _memory_with_case(tmp_path)
    interp = MockLLM([hyp(), plan()])
    o = Tote(TOY / "toy_off_by_one", tmp_path / "runs", interp,
             MockLLM([act("apply_patch", **FIX), finish()]),
             cfg=ToteConfig(memory_mode="on", case_threshold=1.01), memory=mem).run()
    assert o.solved and o.cases_retrieved == 0 and "past case" not in interp.calls[0]["messages"][0]["content"]
    with pytest.raises(ValueError):
        Tote(TOY / "toy_off_by_one", tmp_path / "r2", interp, interp, cfg=ToteConfig(memory_mode="on"))


def test_record_case_after_solve(tmp_path):
    rec = C.CaseMemory(tmp_path / "rec.jsonl")
    interp = MockLLM([hyp(), plan(), {"claim_abstract": "A loop over a range stops one item before the end."}])
    o = Tote(TOY / "toy_off_by_one", tmp_path / "runs", interp,
             MockLLM([act("apply_patch", **FIX), finish()]),
             cfg=ToteConfig(seed=1, model="m"), record=rec).run()
    assert o.solved and o.case_recorded and o.llm_calls == 4   # the abstraction call is not in the task budget
    case = json.loads((tmp_path / "rec.jsonl").read_text())
    assert case["source_task"] == "toy_off_by_one" and case["operation_chain"] == ["local_fix"]
    assert case["hit_hypothesis"]["category"] == "boundaries" and case["signature"]["n_failing"] == 2


def test_record_rejects_leaking_abstract(tmp_path):
    rec = C.CaseMemory(tmp_path / "rec.jsonl")
    interp = MockLLM([hyp(), plan(), {"claim_abstract": "window_sums stops one window early in the loop."}])
    o = Tote(TOY / "toy_off_by_one", tmp_path / "runs", interp,
             MockLLM([act("apply_patch", **FIX), finish()]), cfg=ToteConfig(seed=1), record=rec).run()
    assert o.solved and o.case_recorded is None and not (tmp_path / "rec.jsonl").exists()


def test_memory_and_record_are_exclusive(tmp_path):
    m = C.CaseMemory(tmp_path / "a.jsonl")
    with pytest.raises(ValueError):
        Tote(TOY / "toy_off_by_one", tmp_path / "r", MockLLM([]), MockLLM([]), memory=m, record=m)
