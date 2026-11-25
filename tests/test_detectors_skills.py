from harness import detectors as D
from harness.skills import SkillLibrary, parse_skill

SKILL = """---
name: debug-same-error-loop
version: 2
phase: operate
kind: procedure
triggers: [same_error_x3]
applies_to_operation: [local_fix]
---
## When
Same error three times.
"""


def test_same_error_fires_on_third():
    d = D.TaskDetectors()
    assert d.observe_test("sig", 2) == []
    assert d.observe_test("sig", 2) == []
    assert D.SAME_ERROR in d.observe_test("sig", 2)


def test_no_progress():
    d = D.TaskDetectors(same_error_n=99, no_progress_n=3)
    fired = [d.observe_test(f"s{i}", n) for i, n in enumerate([3, 3, 3, 3])]
    assert D.NO_PROGRESS in fired[-1] and D.NO_PROGRESS not in fired[-2]
    d2 = D.TaskDetectors(same_error_n=99, no_progress_n=3)
    assert all(D.NO_PROGRESS not in d2.observe_test(f"s{i}", n) for i, n in enumerate([3, 3, 2, 2, 1]))


def test_repeat_diff_and_return_to_state():
    d = D.TaskDetectors()
    diff = "--- a/x\n+++ b/x\n-a\n+b\n"
    assert d.observe_diff(diff, "h1") == []
    assert D.REPEAT_DIFF in d.observe_diff(diff, "h2")
    d2 = D.TaskDetectors()
    d2.observe_diff("-a\n+b\n", "h0")
    d2.observe_diff("-b\n+c\n", "h1")
    assert D.REPEAT_DIFF in d2.observe_diff("-c\n+a\n", "h0")
    assert d2.observe_diff("", "h0") == []  # no edit is not a repeated edit


def test_repeat_action_respects_state():
    d = D.SubagentDetectors()
    assert d.observe_action("run_tests", {}, 0) == []
    assert d.observe_action("run_tests", {}, 0) == [D.REPEAT_ACTION]
    assert d.observe_action("run_tests", {}, 1) == []  # after an edit it's a new observation


def test_skill_parse_and_select(tmp_path):
    s = parse_skill(SKILL)
    assert (s.name, s.version, s.triggers) == ("debug-same-error-loop", 2, ["same_error_x3"])
    (tmp_path / "debug-same-error-loop").mkdir()
    (tmp_path / "debug-same-error-loop" / "SKILL.md").write_text(SKILL)
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "SKILL.md").write_text(SKILL.replace("debug-same-error-loop", "meta"))
    lib = SkillLibrary(tmp_path)
    assert list(lib.skills) == ["debug-same-error-loop"]
    assert [x.name for x in lib.select("operate", "local_fix", [])] == ["debug-same-error-loop"]
    assert lib.select("operate", "bisect", []) == []
    assert [x.name for x in lib.select("operate", None, ["same_error_x3"])] == ["debug-same-error-loop"]
    assert SkillLibrary(None).skills == {}
