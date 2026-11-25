"""Schemas shared by the harness and the model (SPEC 3.2, 3.3, 3.4).

Everything the model emits is constrained by one of these schemas on the server
side (JSON schema) and re-validated here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActionName = Literal["read_file", "search", "apply_patch", "run_tests", "run_python", "finish"]
OperationType = Literal["minimal_repro", "add_logging", "local_fix", "bisect", "revert", "read_more_code"]


class Action(BaseModel):
    """One subagent step (SPEC 3.3). `args` keys depend on the action:
    read_file{path,start?,end?} search{pattern,path?} apply_patch{path,old,new}
    run_tests{} run_python{code} finish{status,hypothesis,evidence,changes,confidence,next_hint}
    """
    action: ActionName
    args: dict[str, str]
    thought: str = Field(max_length=400)


class Hypothesis(BaseModel):
    """HYPOTHESIZE output. A hypothesis without a testable prediction is rejected."""
    category: str = Field(min_length=2, max_length=60)
    location: str = Field(min_length=2, max_length=200, description="file:function or file:line")
    claim: str = Field(min_length=5, max_length=400)
    prediction: str = Field(min_length=10, max_length=400,
                            description="what changes in the test output if the claim is true and fixed")


class PlanOperation(BaseModel):
    operation: OperationType
    reason: str = Field(max_length=300)


class StageResult(BaseModel):
    """Stage result contract (SPEC 3.4)."""
    status: Literal["done", "fail", "blocked"]
    hypothesis: str = ""
    evidence: str = ""
    changes: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    next_hint: str | None = None

    @classmethod
    def from_finish_args(cls, args: dict[str, str]) -> "StageResult":
        status = args.get("status", "done")
        if status not in ("done", "fail", "blocked"):
            status = "fail"
        conf = args.get("confidence", "low")
        if conf not in ("low", "medium", "high"):
            conf = "low"
        changes = [c.strip() for c in args.get("changes", "").split(";") if c.strip()]
        return cls(status=status, hypothesis=args.get("hypothesis", ""),
                   evidence=args.get("evidence", ""), changes=changes, confidence=conf,
                   next_hint=args.get("next_hint") or None)


class TestRun(BaseModel):
    """Deterministic TEST_RUN result."""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    collection_error: bool = False
    first_failure: str | None = None       # test node id
    first_message: str | None = None
    traceback: str = ""                     # trimmed to ~40 lines
    signature: str = ""                     # normalized traceback (loop detection)
    timed_out: bool = False

    @property
    def n_failing(self) -> int:
        return self.failed + self.errors + (1 if self.collection_error else 0)

    @property
    def green(self) -> bool:
        return self.n_failing == 0 and not self.timed_out and self.passed > 0
