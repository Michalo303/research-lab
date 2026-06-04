import runpy


def test_audit_hypothesis_dedupe_wrapper_loads_without_running_audit(monkeypatch):
    called = {"audit": False}

    def fake_audit_hypothesis_queue(*args, **kwargs):
        called["audit"] = True
        raise AssertionError("wrapper import must not run hypothesis dedupe audit")

    monkeypatch.setattr(
        "research_lab.hypothesis_dedupe.audit_hypothesis_queue",
        fake_audit_hypothesis_queue,
    )

    namespace = runpy.run_path("scripts/audit_hypothesis_dedupe.py", run_name="__not_main__")

    assert namespace["audit_hypothesis_queue"] is fake_audit_hypothesis_queue
    assert called == {"audit": False}


def test_generate_static_dashboard_wrapper_loads_without_writing_dashboard(monkeypatch):
    called = {"dashboard": False}

    def fake_write_static_dashboard(*args, **kwargs):
        called["dashboard"] = True
        raise AssertionError("wrapper import must not write dashboard output")

    monkeypatch.setattr(
        "research_lab.dashboard.write_static_dashboard",
        fake_write_static_dashboard,
    )

    namespace = runpy.run_path("scripts/generate_static_dashboard.py", run_name="__not_main__")

    assert namespace["write_static_dashboard"] is fake_write_static_dashboard
    assert called == {"dashboard": False}
