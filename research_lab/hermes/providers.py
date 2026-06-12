from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse


DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_PROVIDER_OUTPUT_BYTES = 1_000_000


@dataclass(frozen=True)
class ProviderResult:
    status: str
    output: str | None = None
    message: str = ""


def invoke_provider(
    provider: str,
    prompt: str,
    env: Mapping[str, str],
    *,
    run_command: Callable[..., Any] = subprocess.run,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> ProviderResult:
    provider_name = str(provider or "").strip().lower()
    if provider_name == "command":
        return _invoke_command(prompt, env, run_command)
    if provider_name == "openai_compatible":
        return _invoke_openai_compatible(prompt, env, urlopen)
    return ProviderResult("provider_unavailable", message=f"unsupported Hermes provider: {provider_name or 'not configured'}")


def _invoke_command(prompt: str, env: Mapping[str, str], run_command: Callable[..., Any]) -> ProviderResult:
    command = str(env.get("HERMES_COMMAND", "")).strip()
    if not command:
        return ProviderResult("provider_unavailable", message="HERMES_COMMAND is not configured")
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return ProviderResult("provider_unavailable", message=f"invalid HERMES_COMMAND: {exc}")
    if not argv:
        return ProviderResult("provider_unavailable", message="HERMES_COMMAND is empty")
    try:
        completed = run_command(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=_timeout(env),
            shell=False,
            check=False,
            env=_command_env(env),
        )
    except (FileNotFoundError, PermissionError) as exc:
        return ProviderResult("provider_unavailable", message=_redact(f"Hermes command unavailable: {exc}", env))
    except subprocess.TimeoutExpired:
        return ProviderResult("provider_error", message="Hermes command timed out")
    except OSError as exc:
        return ProviderResult("provider_error", message=_redact(f"Hermes command failed: {exc}", env))
    if completed.returncode != 0:
        message = str(completed.stderr or "Hermes command returned a nonzero exit status").strip()
        return ProviderResult("provider_error", message=_redact(message[:1000], env))
    output = str(completed.stdout or "").strip()
    if not output:
        return ProviderResult("provider_error", message="Hermes command returned empty output")
    if len(output.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
        return ProviderResult("provider_error", message="Hermes command output exceeded the size limit")
    return ProviderResult("ok", output=output)


def _invoke_openai_compatible(prompt: str, env: Mapping[str, str], urlopen: Callable[..., Any]) -> ProviderResult:
    base_url = str(env.get("HERMES_OPENAI_BASE_URL", "")).strip().rstrip("/")
    model = str(env.get("HERMES_OPENAI_MODEL", "")).strip()
    api_key = str(env.get("HERMES_OPENAI_API_KEY", "")).strip()
    if not base_url or not model:
        return ProviderResult(
            "provider_unavailable", message="HERMES_OPENAI_BASE_URL and HERMES_OPENAI_MODEL are required"
        )
    endpoint_error = _validate_openai_base_url(base_url)
    if endpoint_error:
        return ProviderResult("provider_error", message=endpoint_error)
    if not api_key and not _is_loopback(base_url):
        return ProviderResult("provider_unavailable", message="HERMES_OPENAI_API_KEY is required for remote endpoints")
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = urllib.request.Request(f"{base_url}/chat/completions", data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=_timeout(env)) as response:
            raw_response = response.read(MAX_PROVIDER_OUTPUT_BYTES + 1)
            if len(raw_response) > MAX_PROVIDER_OUTPUT_BYTES:
                return ProviderResult("provider_error", message="OpenAI-compatible provider response exceeded the size limit")
            response_payload = json.loads(raw_response.decode("utf-8"))
        content = response_payload["choices"][0]["message"]["content"]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        return ProviderResult(
            "provider_error", message=_redact(f"OpenAI-compatible provider request failed: {_safe_error(exc)}", env)
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, UnicodeDecodeError) as exc:
        return ProviderResult("provider_error", message=f"OpenAI-compatible provider returned an invalid response: {exc}")
    output = str(content or "").strip()
    if not output:
        return ProviderResult("provider_error", message="OpenAI-compatible provider returned empty content")
    return ProviderResult("ok", output=output)


def _timeout(env: Mapping[str, str]) -> float:
    try:
        timeout = float(env.get("HERMES_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return min(max(timeout, 1.0), 600.0)


def _is_loopback(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _validate_openai_base_url(base_url: str) -> str | None:
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return "Invalid HERMES_OPENAI_BASE_URL"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "Invalid HERMES_OPENAI_BASE_URL; expected an absolute HTTP(S) URL"
    if parsed.scheme == "http" and not _is_loopback(base_url):
        return "Remote OpenAI-compatible endpoints require HTTPS; plaintext HTTP is allowed only for loopback hosts"
    return None


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return str(getattr(exc, "reason", exc))[:500]


def _redact(message: str, env: Mapping[str, str]) -> str:
    sanitized = str(message)
    sensitive_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    for name, value in env.items():
        secret = str(value or "")
        if secret and any(marker in str(name).upper() for marker in sensitive_markers):
            sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def _command_env(env: Mapping[str, str]) -> dict[str, str]:
    process_keys = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "XDG_CONFIG_HOME",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in process_keys}
    result.update({str(key): str(value) for key, value in env.items() if str(key).upper().startswith("HERMES_")})
    return result
