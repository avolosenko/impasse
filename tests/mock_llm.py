"""Scripted LLM for harness tests: returns queued JSON replies, validated like the real client."""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from harness.llm import ChatResult, parse_structured


class MockLLM:
    def __init__(self, replies: list[Any] | Callable[[list[dict], dict], Any]):
        self.replies = replies
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], **kw: Any) -> ChatResult:
        self.calls.append({"messages": copy.deepcopy(messages), **kw})
        if callable(self.replies):
            reply = self.replies(messages, kw)
        else:
            if not self.replies:
                raise AssertionError("MockLLM: no more scripted replies")
            reply = self.replies.pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        r = ChatResult(text=text, prompt_tokens=100, completion_tokens=20, latency_s=0.01)
        if kw.get("schema") is not None:
            r.parsed = parse_structured(text, kw["schema"])
        return r


def act(action: str, thought: str = "t", **args: str) -> dict:
    return {"action": action, "args": args, "thought": thought}


def hyp(category="boundaries", location="windows.py:window_sums", claim="loop stops one window early",
        prediction="test_basic and test_whole_list pass after the fix") -> dict:
    return {"category": category, "location": location, "claim": claim, "prediction": prediction}


def plan(op="local_fix") -> dict:
    return {"operation": op, "reason": "fix it"}
