"""Loop detectors (SPEC 3.6). Each has a stable id used as a skill trigger."""

from __future__ import annotations

import json

SAME_ERROR = "same_error_x3"
REPEAT_DIFF = "repeat_diff"
REPEAT_ACTION = "repeat_action"
STEP_LIMIT = "step_limit"
NO_PROGRESS = "no_progress"

ALL = (SAME_ERROR, REPEAT_DIFF, REPEAT_ACTION, STEP_LIMIT, NO_PROGRESS)


def _norm_diff(d: str) -> str:
    return "\n".join(l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))


class TaskDetectors:
    """Detectors over the iteration history of one task (TOTE level)."""

    def __init__(self, same_error_n: int = 3, no_progress_n: int = 4):
        self.same_error_n = same_error_n
        self.no_progress_n = no_progress_n
        self.signatures: list[str] = []
        self.failing: list[int] = []
        self.diffs: list[str] = []
        self.states: list[str] = []      # source snapshot hashes after each iteration

    def observe_test(self, signature: str, n_failing: int) -> list[str]:
        self.signatures.append(signature)
        self.failing.append(n_failing)
        fired = []
        n = self.same_error_n
        if len(self.signatures) >= n and len(set(self.signatures[-n:])) == 1:
            fired.append(SAME_ERROR)
        k = self.no_progress_n
        if len(self.failing) > k and min(self.failing[-k:]) >= self.failing[-k - 1]:
            fired.append(NO_PROGRESS)
        return fired

    def observe_diff(self, diff: str, state_hash: str) -> list[str]:
        """`diff` is the edit made in this iteration; `state_hash` the source state after
        it (and after any rollback). Fires on a repeated edit or a return to an earlier state."""
        nd = _norm_diff(diff)
        fired = []
        if nd and nd in self.diffs:
            fired.append(REPEAT_DIFF)
        elif nd and state_hash in self.states[:-1]:
            fired.append(REPEAT_DIFF)
        self.diffs.append(nd)
        self.states.append(state_hash)
        return fired


class SubagentDetectors:
    """Detectors inside one subagent run."""

    def __init__(self, repeat_n: int = 2):
        self.repeat_n = repeat_n
        self.seen: dict[str, int] = {}

    def observe_action(self, action: str, args: dict, state: int = 0) -> list[str]:
        """`state` is a workspace mutation counter: re-reading a file or re-running tests
        after an edit is not a repeat."""
        key = f"{state}|{action}|{json.dumps(args, sort_keys=True)}"
        self.seen[key] = self.seen.get(key, 0) + 1
        return [REPEAT_ACTION] if self.seen[key] >= self.repeat_n else []
