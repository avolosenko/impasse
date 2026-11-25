"""Minimal client for llama-server's OpenAI-compatible chat API.

Structured output is enforced by the server (JSON schema / GBNF grammar),
never by trusting the model. Every call returns token usage and latency so the
harness can account for budgets and log everything.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from harness.config import Config

StructuredMode = Literal["json_schema", "json_object", "grammar"]


class LLMError(RuntimeError):
    pass


class StructuredOutputError(LLMError):
    """Server returned text that is not valid JSON / does not match the schema."""

    def __init__(self, msg: str, raw: str):
        super().__init__(msg)
        self.raw = raw


@dataclass
class ChatResult:
    text: str
    parsed: Any = None
    reasoning: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    finish_reason: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def tokens_per_s(self) -> float:
        return self.completion_tokens / self.latency_s if self.latency_s > 0 else 0.0


def schema_of(model_or_schema: type[BaseModel] | dict) -> tuple[str, dict]:
    if isinstance(model_or_schema, dict):
        return model_or_schema.get("title", "output"), model_or_schema
    return model_or_schema.__name__, model_or_schema.model_json_schema()


class LLMClient:
    def __init__(
        self,
        base_url: str,
        *,
        temperature: float = 0.65,
        top_p: float = 0.95,
        max_tokens: int = 1024,
        enable_thinking: bool = False,
        structured_mode: StructuredMode = "json_schema",
        timeout_s: float = 600,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.structured_mode = structured_mode
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)

    @classmethod
    def for_role(cls, cfg: Config, role: str, **overrides) -> "LLMClient":
        llm = cfg.get("llm", {})
        kw = dict(
            temperature=llm.get("temperature", 0.65),
            top_p=llm.get("top_p", 0.95),
            max_tokens=llm.get("max_tokens", 1024),
            enable_thinking=llm.get("enable_thinking", False),
            structured_mode=llm.get("structured_output", "json_schema"),
            timeout_s=llm.get("request_timeout_s", 600),
        )
        kw.update(overrides)
        return cls(cfg.endpoint_for_role(role), **kw)

    def close(self) -> None:
        self._http.close()

    # --- request building --------------------------------------------------

    def build_payload(
        self,
        messages: list[dict],
        *,
        schema: type[BaseModel] | dict | None = None,
        grammar: str | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        mode: StructuredMode | None = None,
        extra: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "top_p": self.top_p,
            "max_tokens": max_tokens or self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }
        if seed is not None:
            payload["seed"] = seed
        mode = mode or self.structured_mode
        if grammar is not None:
            payload["grammar"] = grammar
        elif schema is not None:
            name, js = schema_of(schema)
            if mode == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": js, "strict": True},
                }
            elif mode == "json_object":
                payload["response_format"] = {"type": "json_object", "schema": js}
            elif mode == "grammar":
                raise ValueError("mode 'grammar' needs an explicit `grammar` (GBNF) argument")
            else:
                raise ValueError(f"unknown structured mode {mode!r}")
        if extra:
            payload.update(extra)
        return payload

    # --- calls -------------------------------------------------------------

    def chat(self, messages: list[dict], **kw) -> ChatResult:
        schema = kw.get("schema")
        payload = self.build_payload(messages, **kw)
        t0 = time.monotonic()
        try:
            r = self._http.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"request to {self.base_url} failed: {e}") from e
        latency = time.monotonic() - t0
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        usage = data.get("usage", {})
        res = ChatResult(
            text=msg.get("content") or "",
            reasoning=msg.get("reasoning_content"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_s=latency,
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )
        if schema is not None or kw.get("grammar") is not None:
            res.parsed = parse_structured(res.text, schema)
        return res

    def props(self) -> dict:
        r = self._http.get("/props")
        r.raise_for_status()
        return r.json()

    def slots(self) -> list[dict] | None:
        r = self._http.get("/slots")
        return r.json() if r.status_code == 200 else None


def parse_structured(text: str, schema: type[BaseModel] | dict | None) -> Any:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise StructuredOutputError(f"not valid JSON: {e}", text) from e
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        try:
            return schema.model_validate(obj)
        except ValidationError as e:
            raise StructuredOutputError(f"schema mismatch: {e}", text) from e
    return obj
