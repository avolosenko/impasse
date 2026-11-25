from pathlib import Path

import pytest
import yaml

from harness.config import Config, REPO_ROOT, deep_merge


def test_deep_merge_nested_and_lists_replaced():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    out = deep_merge(base, {"a": {"y": [3]}, "c": 2})
    assert out == {"a": {"x": 1, "y": [3]}, "b": 1, "c": 2}
    assert base["a"]["y"] == [1, 2]  # input untouched


def test_role_endpoints(tmp_config):
    port_main = tmp_config.server("main")["port"]
    assert tmp_config.endpoint_for_role("interpreter") == f"http://127.0.0.1:{port_main}"
    assert tmp_config.endpoint_for_role("worker") == tmp_config.server_url("aux")
    with pytest.raises(KeyError):
        tmp_config.endpoint_for_role("nope")


def test_gguf_path_from_local(tmp_config):
    assert tmp_config.gguf_path("m-a").name == "a.gguf"
    with pytest.raises(KeyError, match="unknown model"):
        tmp_config.gguf_path("missing")


def test_local_overrides_shared(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"llm": {"temperature": 0.6, "top_p": 0.9}}))
    (tmp_path / "config.local.yaml").write_text(yaml.safe_dump({"llm": {"temperature": 0.7}}))
    cfg = Config.load(tmp_path)
    assert cfg["llm"] == {"temperature": 0.7, "top_p": 0.9}
    assert Config.load(tmp_path, local=False)["llm"]["temperature"] == 0.6


def test_repo_config_is_valid_and_machine_independent():
    cfg = Config.load(REPO_ROOT, local=False)
    for role in cfg["roles"]:
        cfg.endpoint_for_role(role)
    text = (REPO_ROOT / "config.yaml").read_text()
    for bad in ("/home/", "/mnt/c/", "C:\\"):
        assert bad not in text
    for name, m in cfg["models"].items():
        assert "-m" not in m["args"], name
        assert str(m["parallel"]) == m["args"][m["args"].index("--parallel") + 1], name
