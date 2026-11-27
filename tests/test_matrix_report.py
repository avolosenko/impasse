import csv
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import make_report  # noqa: E402
import run_matrix  # noqa: E402
from harness.results import FIELDS  # noqa: E402
from tests.mock_llm import MockLLM, act, hyp, plan  # noqa: E402

TOY = ROOT / "bench" / "toy"


def test_load_matrix_applies_common_and_rejects_unknown(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("series: s\nmodels: [m]\nseeds: [1]\nsplits: [train]\ncommon: {max_llm_calls: 9}\n"
                 "configs:\n  - {id: B1}\n  - {id: B0, b0: true, max_llm_calls: 5}\n")
    m = run_matrix.load_matrix(p)
    assert [c["max_llm_calls"] for c in m["configs"]] == [9, 5]
    p.write_text("series: s\nmodels: [m]\nseeds: [1]\nsplits: [train]\nconfigs:\n  - {id: X, typo: 1}\n")
    with pytest.raises(ValueError, match="unknown keys"):
        run_matrix.load_matrix(p)


def test_jobs_skip_done(tmp_path):
    m = {"configs": [{"id": "B1"}, {"id": "B0", "b0": True}], "seeds": [1, 2]}
    split = tmp_path / "train"
    for name in ("t1", "t2"):
        (split / name).mkdir(parents=True)
        (split / name / "task.yaml").write_text("id: x\n")
    done = {("t1", "B1", "m", 1)}
    jobs = run_matrix.jobs_for(m, "m", {"train": split}, done)
    assert len(jobs) == 2 * 2 * 2 - 1
    assert not any(j["task"].name == "t1" and j["config"]["id"] == "B1" and j["seed"] == 1 for j in jobs)


def test_done_keys_roundtrip(tmp_path):
    p = tmp_path / "r.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerow({k: "" for k in FIELDS} | {"task_id": "t", "config": "B1", "model": "m", "seed": "3"})
    assert run_matrix.done_keys(p) == {("t", "B1", "m", 3)}
    assert run_matrix.done_keys(tmp_path / "missing.csv") == set()


def test_run_job_writes_row_and_no_escalation_for_sealed_split(tmp_path, monkeypatch):
    fix = dict(path="windows.py", old="range(len(xs) - k)", new="range(len(xs) - k + 1)")
    replies = {"interpreter": [hyp(), plan()], "worker": [act("apply_patch", **fix),
                                                          act("finish", status="done")]}
    monkeypatch.setattr(run_matrix.LLMClient, "for_role",
                        classmethod(lambda cls, cfg, role, **kw: MockLLM(replies[role])))
    monkeypatch.setattr(run_matrix, "ROOT", tmp_path)
    job = {"config": {"id": "B1"}, "seed": 1, "split": "test", "task": TOY / "toy_off_by_one", "model": "m"}
    r = run_matrix.run_job(job, cfg=None, series="s", csv_path=tmp_path / "r.csv", lock=threading.Lock())
    assert r["solved"] == 1 and r["split"] == "test" and r["config"] == "B1"
    assert run_matrix.done_keys(tmp_path / "r.csv") == {("toy_off_by_one", "B1", "m", 1)}
    assert not (tmp_path / "escalations").exists()


def _rows(spec):
    rows = []
    for cfg, task, seed, solved in spec:
        r = {k: "" for k in FIELDS}
        r.update(config=cfg, model="m", split="train", task_id=task, seed=str(seed), solved=str(solved),
                 total_llm_calls="10", detector_fires='{"step_limit": 1}' if not solved else "{}",
                 escalated=str(1 - solved), category="c1", difficulty="easy",
                 skills_loaded="local-fix" if cfg == "B2" else "", hypothesis_hit="1", first_hit_iteration="1")
        rows.append(r)
    return rows


def test_per_task_rates_average_seeds_first():
    rows = _rows([("B1", "a", 1, 1), ("B1", "a", 2, 0), ("B1", "b", 1, 1), ("B1", "b", 2, 1)])
    assert make_report.per_task_rates(rows) == {"a": 0.5, "b": 1.0}


def test_bootstrap_ci_bounds():
    lo, hi = make_report.bootstrap_ci([0.0, 1.0] * 10)
    assert 0.25 < lo < 0.5 < hi < 0.75
    assert make_report.bootstrap_ci([1.0] * 5) == (1.0, 1.0)


def test_render_has_all_sections():
    rows = _rows([("B1", "a", 1, 1), ("B1", "b", 1, 0), ("B2", "a", 1, 1), ("B2", "b", 1, 1)])
    text = make_report.render(rows, "t")
    for section in ("## Solve rate", "## By category", "## By difficulty", "## Detectors", "## Skills", "## Hypotheses"):
        assert section in text
    assert "| B2 | m | train | 2 | 2 | 100% |" in text
    assert "| B2 | m | train | local-fix | 2 | 100% |" in text


def test_suspicious_undefined_for_b0():
    rows = _rows([("B0", "a", 1, 1)])
    for r in rows:
        r["orchestration"] = "B0"
    assert "| B0 | m | train | 1 | 1 | 100% |" in make_report.render(rows, "t")
    assert make_report.solve_table(rows)[-1].endswith("| — |")
