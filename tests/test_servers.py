import pytest

from harness import servers


def test_build_command(tmp_config):
    cmd = servers.build_command(tmp_config, "m-a", "main", extra_args=["-ctk", "q8_0"])
    port = str(tmp_config.server("main")["port"])
    assert cmd[0].endswith("fake-llama-server")
    assert cmd[1:3] == ["-m", str(tmp_config.gguf_path("m-a"))]
    assert cmd[3:7] == ["--host", "127.0.0.1", "--port", port]
    assert cmd[7:] == ["-c", "8192", "--parallel", "1", "-ctk", "q8_0"]


def test_build_command_rejects_managed_flags(tmp_config):
    with pytest.raises(ValueError, match="--port"):
        servers.build_command(tmp_config, "m-a", args_override=["--port", "1"])


def test_start_status_switch_stop(tmp_config):
    assert servers.status(tmp_config, "main")["process"] == "none"
    try:
        st = servers.start(tmp_config, "m-a", "main")
        assert st["startup_s"] >= 0.3  # waited through the 'loading' phase
        s = servers.status(tmp_config, "main")
        assert (s["process"], s["health"], s["model"]) == ("running", "ok", "m-a")

        with pytest.raises(RuntimeError, match="already running"):
            servers.start(tmp_config, "m-b", "main")

        sw = servers.switch(tmp_config, "m-b", "main")
        assert sw["model"] == "m-b" and sw["pid"] != st["pid"] and "switch_s" in sw
        assert servers.status(tmp_config, "main")["model"] == "m-b"
    finally:
        servers.stop(tmp_config, "main")
    s = servers.status(tmp_config, "main")
    assert s["process"] == "none" and s["health"] == "down"
    assert servers.stop(tmp_config, "main") is False


def test_two_servers_independent(tmp_config):
    try:
        servers.start(tmp_config, "m-a", "main")
        servers.start(tmp_config, "m-b", "aux")
        assert servers.status(tmp_config, "aux")["model"] == "m-b"
        servers.stop(tmp_config, "main")
        assert servers.status(tmp_config, "aux")["health"] == "ok"
    finally:
        servers.stop(tmp_config, "main")
        servers.stop(tmp_config, "aux")


def test_missing_gguf(tmp_config, tmp_path):
    (tmp_path / "a.gguf").unlink()
    with pytest.raises(FileNotFoundError, match="GGUF"):
        servers.start(tmp_config, "m-a", "main")


def test_startup_crash_reports_log(tmp_config, tmp_path):
    bad = tmp_path / "crash-server"
    bad.write_text("#!/bin/sh\necho 'CUDA error: out of memory'\nexit 1\n")
    bad.chmod(0o755)
    tmp_config.data["llama_server_bin"] = str(bad)
    with pytest.raises(RuntimeError, match="out of memory"):
        servers.start(tmp_config, "m-a", "main")
    servers.stop(tmp_config, "main")
