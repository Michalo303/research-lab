import json
import subprocess
import urllib.error

import pytest

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


def test_openai_compatible_rejects_remote_plaintext_http_before_transport():
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise AssertionError("remote plaintext HTTP must be rejected before transport")

    result = invoke_provider(
        "openai_compatible",
        "prompt",
        {
            "HERMES_OPENAI_BASE_URL": "http://remote.example/v1",
            "HERMES_OPENAI_MODEL": "model",
            "HERMES_OPENAI_API_KEY": "must-not-be-sent",
        },
        urlopen=fake_urlopen,
    )

    assert result.status == "provider_error"
    assert result.output is None
    assert "HTTPS" in result.message
    assert "must-not-be-sent" not in result.message
    assert calls == []


def test_openai_compatible_malformed_base_url_returns_provider_error():
    result = invoke_provider(
        "openai_compatible",
        "prompt",
        {
            "HERMES_OPENAI_BASE_URL": "not-a-url",
            "HERMES_OPENAI_MODEL": "model",
            "HERMES_OPENAI_API_KEY": "secret-value",
        },
    )

    assert result.status == "provider_error"
    assert result.output is None
    assert "invalid" in result.message.lower()
    assert "secret-value" not in result.message


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


def test_openai_compatible_remote_missing_api_key_has_fixed_reason():
    result = invoke_provider(
        "openai_compatible",
        "prompt",
        {
            "HERMES_OPENAI_BASE_URL": "https://api.example.test/v1",
            "HERMES_OPENAI_MODEL": "model",
        },
    )

    assert result.status == "provider_unavailable"
    assert result.reason == "missing_api_key"


@pytest.mark.parametrize(
    ("code", "body", "expected_reason"),
    [
        (401, b'{"error":{"message":"bad key"}}', "authentication_failure"),
        (403, b'{"error":{"message":"forbidden"}}', "authentication_failure"),
        (403, b'{"error":{"message":"insufficient quota"}}', "quota_exceeded"),
        (404, b'{"error":{"message":"model not found"}}', "model_unavailable"),
        (403, b'{"error":{"message":"model access denied"}}', "model_unavailable"),
        (429, b'{"error":{"message":"rate limit exceeded"}}', "rate_limited"),
        (429, b'{"error":{"message":"insufficient quota"}}', "quota_exceeded"),
        (400, b'{"error":{"message":"unsupported model"}}', "model_unavailable"),
        (418, b'{"error":{"message":"teapot"}}', "http_4xx"),
        (500, b'{"error":{"message":"server error"}}', "http_5xx"),
    ],
)
def test_openai_compatible_classifies_http_failures_to_fixed_reasons(
    code, body, expected_reason
):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            code,
            "provider failure",
            hdrs=None,
            fp=_Response(json.loads(body.decode("utf-8"))),
        )

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
    assert result.reason == expected_reason
    assert "do-not-log-this" not in result.message


def test_openai_compatible_classifies_timeout_without_exposing_provider_text():
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out while contacting provider")

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
    assert result.reason == "timeout"
    assert "do-not-log-this" not in result.message


def test_openai_compatible_classifies_network_error_without_exposing_provider_text():
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
    assert result.reason == "network_error"
    assert "do-not-log-this" not in result.message


def test_openai_compatible_classifies_malformed_response_to_fixed_reason():
    def fake_urlopen(request, timeout):
        return _Response({"choices": []})

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
    assert result.reason == "malformed_response"
    assert "do-not-log-this" not in result.message


def test_openai_compatible_classifies_empty_content_to_fixed_reason():
    def fake_urlopen(request, timeout):
        return _Response({"choices": [{"message": {"content": "   "}}]})

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
    assert result.reason == "empty_response"
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
