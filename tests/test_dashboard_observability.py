from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from research_lab import dashboard
from research_lab import dashboard_server


def test_dashboard_renders_required_sections_without_artifacts(tmp_path):
    html = dashboard.render_dashboard_html(tmp_path)

    assert "READ ONLY MODE" in html
    assert "Live Status" in html
    assert "Research Results" in html
    assert "Portfolio / Paper Readiness" in html
    assert "Sentiment / Attention" in html
    assert "Data / Edge Audit" in html
    assert "Improvement Ideas" in html
    assert "Alerts / Errors" in html
    assert "No portfolio equity data available" in html
    assert "missing" in html.lower()


def test_dashboard_renders_equity_svg_when_equity_csv_exists(tmp_path):
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    (weekly / "2026-W21_portfolio_equity.csv").write_text(
        "date,equity,return\n"
        "2026-05-20,100.0,0.0\n"
        "2026-05-21,110.0,0.1\n"
        "2026-05-22,105.0,-0.0454545454\n",
        encoding="utf-8",
    )

    html = dashboard.render_dashboard_html(tmp_path)

    assert 'id="equity-chart"' in html
    assert "drawdown" in html.lower()
    assert "No portfolio equity data available" not in html


def test_dashboard_redacts_sensitive_values(tmp_path):
    (tmp_path / ".env").write_text(
        "API_KEY=very-secret-value\nTOKEN=super-token\nPASSWORD=topsecret\n",
        encoding="utf-8",
    )
    execution = tmp_path / "reports" / "execution"
    execution.mkdir(parents=True)
    (execution / "ibkr_paper_read_only_snapshot.json").write_text(
        json.dumps(
            {
                "account": "jcbfp583",
                "read_only": True,
                "token": "should-not-leak",
                "status": "ok",
            }
        ),
        encoding="utf-8",
    )

    html = dashboard.render_dashboard_html(tmp_path)

    assert "API_KEY" not in html
    assert "TOKEN" not in html
    assert "SECRET" not in html
    assert "PASSWORD" not in html
    assert "very-secret-value" not in html
    assert "super-token" not in html
    assert "topsecret" not in html
    assert "should-not-leak" not in html
    assert "jcbfp583" not in html


def test_dashboard_server_serves_html_and_blocks_writes(tmp_path):
    server = dashboard_server.create_dashboard_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        html = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        assert "READ ONLY MODE" in html

        data = urllib.request.urlopen(f"{base}/api/refresh", timeout=5).read().decode("utf-8")
        payload = json.loads(data)
        assert payload["read_only_mode"] is True

        request = urllib.request.Request(f"{base}/api/actions", method="POST")
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("POST to action endpoint unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            assert "disabled" in exc.read().decode("utf-8").lower()

        request = urllib.request.Request(f"{base}/api/sentiment/run", method="POST")
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("POST to sentiment endpoint unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_deploy_templates_exist():
    nginx_conf = Path("deploy/nginx/research-lab-dashboard.conf")
    systemd_service = Path("deploy/systemd/research-lab-dashboard.service")

    assert nginx_conf.exists()
    assert systemd_service.exists()

    nginx_text = nginx_conf.read_text(encoding="utf-8")
    assert "server_name lab.reproscore.com" in nginx_text
    assert "auth_basic" in nginx_text
    assert "proxy_pass http://127.0.0.1:8787" in nginx_text

    service_text = systemd_service.read_text(encoding="utf-8")
    assert "research_lab.dashboard_server" in service_text
    assert "127.0.0.1" in service_text
    assert "8787" in service_text
