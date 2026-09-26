"""Dashboard page links (no services needed)."""
from fastapi.testclient import TestClient

from gemmanet.dashboard.app import dashboard_app


def test_links_default_to_same_origin(monkeypatch):
    monkeypatch.delenv('GEMMANET_SITE_URL', raising=False)
    page = TestClient(dashboard_app).get('/').text
    assert 'href="/">Home' in page
    assert 'href="/docs/">Docs' in page


def test_links_point_to_main_site_when_it_is_elsewhere(monkeypatch):
    monkeypatch.setenv('GEMMANET_SITE_URL', 'https://gemmanet.net')
    monkeypatch.setenv('COORDINATOR_URL', 'https://api.gemmanet.net')
    page = TestClient(dashboard_app).get('/').text
    assert 'href="https://gemmanet.net">Home' in page
    assert 'href="https://gemmanet.net/docs/">Docs' in page
    assert "const BASE = 'https://api.gemmanet.net'" in page
