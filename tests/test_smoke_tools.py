"""Offline checks of the stage-1 tools (no GPU / llama-server needed)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import estimate_matrix  # noqa: E402
import smoke  # noqa: E402


def test_action_schema_matches_protocol():
    a = smoke.Action.model_validate({"action": "read_file", "args": {"path": "x.py"}, "thought": "t"})
    assert a.action == "read_file"
    enum = smoke.Action.model_json_schema()["properties"]["action"]["enum"]
    assert set(enum) == {"read_file", "search", "apply_patch", "run_tests", "run_python", "finish"}


def test_gbnf_mentions_every_action():
    for act in ("read_file", "search", "apply_patch", "run_tests", "run_python", "finish"):
        assert f'\\"{act}\\"' in smoke.ACTION_GBNF


def test_estimate_per_task():
    smoke_rep = {
        "server_info": {"total_slots": 2},
        "throughput": {"n2": {"per_request_tok_s": 50.0}},
        "long_prompt": {"prompt_tok_s": 2000.0},
    }
    # 10 calls * (1000 prompt / (2000/2) + 100 / 50) = 10 * (1 + 2) = 30 s
    assert estimate_matrix.per_task_seconds(smoke_rep, 10, 1000, 100, 0.0) == 30.0


def test_servers_sh_is_executable():
    import os
    assert os.access(ROOT / "tools" / "servers.sh", os.X_OK)
