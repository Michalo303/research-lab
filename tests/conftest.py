import urllib.request

import pytest


@pytest.fixture
def hermetic_provider_guard(monkeypatch):
    """Fail closed if selector/runner tests reach any live-capable provider path."""
    for name in ("EODHD_API_KEY", "MASSIVE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def blocked(*args, **kwargs):
        raise AssertionError("live provider/network path invoked by a hermetic test")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)

    import research_lab.hermes.providers as hermes_providers
    import research_lab.runner as runner

    monkeypatch.setattr(runner, "load_eodhd_daily_universe", blocked)
    monkeypatch.setattr(runner, "load_massive_daily_universe", blocked)
    monkeypatch.setattr(hermes_providers, "_invoke_openai_compatible", blocked)
    return blocked
