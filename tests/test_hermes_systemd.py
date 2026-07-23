import re
from pathlib import Path


SERVICE = Path("ops/systemd/hermes-hypothesis.service")
TIMER = Path("ops/systemd/hermes-hypothesis.timer")
DAILY_TIMER = Path("deploy/systemd/trading-research-daily.timer")


def test_research_job_units_are_locked_hardened_and_bounded():
    for job in ("daily", "weekly", "self-improvement"):
        text = Path(f"deploy/systemd/trading-research-{job}.service").read_text(encoding="utf-8")

        assert "EnvironmentFile=-/opt/trading/research-lab/.env" in text
        assert "/usr/bin/flock -n /opt/trading/research-lab/tmp/research.lock" in text
        assert "NoNewPrivileges=true" in text
        assert "ProtectSystem=full" in text
        assert "ProtectHome=true" in text
        assert "ReadWritePaths=/opt/trading/research-lab" in text
        assert "TimeoutStartSec=" in text


def test_service_uses_safe_user_directory_lock_and_module_entrypoint():
    text = SERVICE.read_text(encoding="utf-8")

    assert "User=trading" in text
    assert "Group=trading" in text
    assert "WorkingDirectory=/opt/trading/research-lab" in text
    assert "EnvironmentFile=-/opt/trading/research-lab/.env" in text
    assert "/usr/bin/flock -n /opt/trading/research-lab/tmp/research.lock" in text
    assert "-m research_lab.hermes.run_hypothesis_generation" in text
    assert "--root /opt/trading/research-lab" in text


def test_systemd_templates_contain_no_embedded_secret_configuration():
    text = SERVICE.read_text(encoding="utf-8") + TIMER.read_text(encoding="utf-8")

    for forbidden in ("API_KEY=", "TOKEN=", "PASSWORD=", "SECRET=", "Authorization:"):
        assert forbidden not in text


def test_hermes_timer_runs_before_daily_research_timer():
    hermes_text = TIMER.read_text(encoding="utf-8")
    daily_text = DAILY_TIMER.read_text(encoding="utf-8")

    assert "OnCalendar=*-*-* 02:00:00 UTC" in hermes_text
    assert "Persistent=true" in hermes_text
    hermes_hour, hermes_minute = _calendar_time(hermes_text)
    daily_hour, daily_minute = _calendar_time(daily_text)
    assert (hermes_hour, hermes_minute) < (daily_hour, daily_minute)


def _calendar_time(text):
    match = re.search(r"OnCalendar=.*\s(\d{2}):(\d{2}):\d{2}\sUTC", text)
    assert match
    return int(match.group(1)), int(match.group(2))
