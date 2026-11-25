import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

from harness.llm import LLMClient, LLMError, StructuredOutputError


class Act(BaseModel):
    action: Literal["read_file", "finish"]
    thought: str


def make_client(reply: str | dict, status: int = 200, captured: list | None = None, **kw):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content))
        if isinstance(reply, dict):
            return httpx.Response(status, json=reply)
        body = {
            "choices": [{"message": {"content": reply}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        return httpx.Response(status, json=body)

    return LLMClient("http://fake:8080", transport=httpx.MockTransport(handler), **kw)


def test_plain_chat_returns_text_and_usage():
    c = make_client("hello")
    r = c.chat([{"role": "user", "content": "hi"}])
    assert r.text == "hello" and r.parsed is None
    assert (r.prompt_tokens, r.completion_tokens) == (11, 7)


def test_json_schema_payload_and_parse():
    sent = []
    c = make_client('{"action": "finish", "thought": "done"}', captured=sent)
    r = c.chat([{"role": "user", "content": "x"}], schema=Act, seed=3)
    assert isinstance(r.parsed, Act) and r.parsed.action == "finish"
    p = sent[0]
    assert p["seed"] == 3
    assert p["response_format"]["type"] == "json_schema"
    assert p["response_format"]["json_schema"]["schema"]["properties"]["action"]["enum"] == ["read_file", "finish"]
    assert p["chat_template_kwargs"] == {"enable_thinking": False}


def test_json_object_mode_payload():
    sent = []
    c = make_client('{"action": "read_file", "thought": "t"}', captured=sent, structured_mode="json_object")
    c.chat([{"role": "user", "content": "x"}], schema=Act)
    assert sent[0]["response_format"]["type"] == "json_object"
    assert "schema" in sent[0]["response_format"]


def test_grammar_payload():
    sent = []
    c = make_client('{"a": 1}', captured=sent)
    r = c.chat([{"role": "user", "content": "x"}], grammar='root ::= "{}"')
    assert sent[0]["grammar"] == 'root ::= "{}"' and "response_format" not in sent[0]
    assert r.parsed == {"a": 1}


def test_grammar_mode_without_grammar_is_error():
    c = make_client("{}", structured_mode="grammar")
    with pytest.raises(ValueError):
        c.chat([{"role": "user", "content": "x"}], schema=Act)


@pytest.mark.parametrize("bad", ['not json', '{"action": "delete_all", "thought": "x"}'])
def test_structured_output_errors_keep_raw_text(bad):
    c = make_client(bad)
    with pytest.raises(StructuredOutputError) as ei:
        c.chat([{"role": "user", "content": "x"}], schema=Act)
    assert ei.value.raw == bad


def test_http_error_raises():
    c = make_client({"error": "boom"}, status=500)
    with pytest.raises(LLMError, match="HTTP 500"):
        c.chat([{"role": "user", "content": "x"}])


def test_reasoning_content_captured():
    body = {"choices": [{"message": {"content": "42", "reasoning_content": "thinking..."}}], "usage": {}}
    c = make_client(body)
    r = c.chat([{"role": "user", "content": "x"}])
    assert r.reasoning == "thinking..." and r.text == "42"


def test_for_role_uses_config(tmp_config):
    c = LLMClient.for_role(tmp_config, "worker", max_tokens=55)
    assert c.base_url == tmp_config.server_url("aux")
    assert c.temperature == 0.6 and c.max_tokens == 55
