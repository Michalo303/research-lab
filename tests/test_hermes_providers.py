import json
import subprocess
import urllib.error

from research_lab.hermes.providers import invoke_provider


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


def test_command_provider_requires_configured_command():
    result = invoke_provider("command", "prompt", {})

    assert result.status == "provider_unavailable"
    assert result.output is None


def test_command_provider_uses_stdin_and_never_uses_shell():
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout='{"hypotheses": []}', stderr="")

    result = invoke_provider(
        "command",
        "safe prompt",
        {
            "HERMES_COMMAND": "hermes-agent --json",
            "HERMES_TIMEOUT_SECONDS": "12",
            "HERMES_AGENT_TOKEN": "allowed-for-provider",
            "EODHD_API_KEY": "must-not-be-inherited",
            "IBKR_ACCOUNT": "must-not-be-inherited",
        },
        run_command=fake_run,
    )

    assert result.status == "ok"
    assert result.output == '{"hypotheses": []}'
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["input"] == "safe prompt"
    assert calls[0][1]["timeout"] == 12.0
    assert calls[0][1]["env"]["HERMES_AGENT_TOKEN"] == "allowed-for-provider"
    assert "EODHD_API_KEY" not in calls[0][1]["env"]
    assert "IBKR_ACCOUNT" not in calls[0][1]["env"]


def test_command_provider_reports_nonzero_exit_without_output():
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 4, stdout="", stderr="provider failed")

    result = invoke_provider("command", "prompt", {"HERMES_COMMAND": "agent"}, run_command=fake_run)

    assert result.status == "provider_error"
    assert result.output is None
    assert "provider failed" in result.message


def test_command_provider_redacts_credentials_from_stderr():
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 4, stdout="", stderr="failed with secret-value")

    result = invoke_provider(
        "command",
        "prompt",
        {"HERMES_COMMAND": "agent", "HERMES_OPENAI_API_KEY": "secret-value"},
        run_command=fake_run,
    )

    assert "secret-value" not in result.message
    assert "[REDACTED]" in result.message


def test_openai_compatible_extracts_message_content():
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response({"choices": [{"message": {"content": '{"hypotheses": []}'}}]})

    result = invoke_provider(
        "openai_compatible",
        "safe prompt",
        {
            "HERMES_OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            "HERMES_OPENAI_MODEL": "test/model",
            "HERMES_OPENAI_API_KEY": "secret-value",
        },
        urlopen=fake_urlopen,
    )

    assert result.status == "ok"
    assert result.output == '{"hypotheses": []}'
    assert requests[0][0].full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert requests[0][0].get_header("Authorization") == "Bearer secret-value"
    assert b"safe prompt" in requests[0][0].data


def test_openai_compatible_requires_key_for_remote_endpoint():
    result = invoke_provider(
        "openai_compatible",
        "prompt",
        {"HERMES_OPENAI_BASE_URL": "https://api.example.test/v1", "HERMES_OPENAI_MODEL": "model"},
    )

    assert result.status == "provider_unavailable"


def test_openai_compatible_allows_keyless_loopback_endpoint():
    def fake_urlopen(request, timeout):
        assert request.get_header("Authorization") is None
        return _Response({"choices": [{"message": {"content": '{"hypotheses": []}'}}]})

    result = invoke_provider(
        "openai_compatible",
        "prompt",
        {"HERMES_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1", "HERMES_OPENAI_MODEL": "qwen"},
        urlopen=fake_urlopen,
    )

    assert result.status == "ok"


def test_openai_provider_error_does_not_expose_api_key():
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    result = invoke_provider(
        "openai_compatible",
        "prompt",
        {
            "HERMES_OPENAI_BASE_URL": "https://api.example.test/v1",
            "HERMES_OPENAI_MODEL": "model",
            "HERMES_OPENAI_API_KEY": "do-not-log-this",
        },
        urlopen=fake_urlopen,
    )

    assert result.status == "provider_error"
    assert "do-not-log-this" not in result.message


def test_unsupported_provider_fails_safely():
    result = invoke_provider("arbitrary_python", "prompt", {})

    assert result.status == "provider_unavailable"


def test_command_provider_rejects_oversized_output():
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="x" * 1_000_001, stderr="")

    result = invoke_provider("command", "prompt", {"HERMES_COMMAND": "agent"}, run_command=fake_run)

    assert result.status == "provider_error"
    assert "size limit" in result.message
