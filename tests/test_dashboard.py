"""Dashboard page: links and Content-Security-Policy (no services needed)."""
import re
from pathlib import Path

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
    assert 'data-coordinator-url="https://api.gemmanet.net"' in page


def test_page_forbids_inline_scripts(monkeypatch):
    monkeypatch.setenv('COORDINATOR_URL', 'https://api.gemmanet.net')
    resp = TestClient(dashboard_app).get('/')
    policy = resp.headers['content-security-policy']
    assert "script-src 'self';" in policy
    assert "connect-src 'self' https://api.gemmanet.net;" in policy
    assert "frame-ancestors 'none'" in policy
    # Anything inline would be blocked by that policy, so there must be none.
    assert not re.search(r'<script(?![^>]*\ssrc=)[^>]*>', resp.text)
    assert not re.search(r'\son[a-z]+\s*=', resp.text)


def test_script_is_served_and_wires_every_button():
    client = TestClient(dashboard_app)
    page = client.get('/').text
    src = re.search(r'<script src="([^"]+)"', page).group(1)
    assert src.startswith('/dashboard/static/dashboard.js')
    js = client.get(src.removeprefix('/dashboard')).text
    assert 'document.currentScript.dataset.coordinatorUrl' in js
    for button_id in re.findall(r'<button[^>]*id="([^"]+)"', page):
        assert f"getElementById('{button_id}').addEventListener('click'" in js


def test_script_is_packaged():
    import tomllib
    config = tomllib.loads((Path(__file__).parent.parent / 'pyproject.toml').read_text())
    assert 'dashboard/static/*.js' in config['tool']['setuptools']['package-data']['gemmanet']


def test_coordinator_url_cannot_break_out_of_the_attribute(monkeypatch):
    monkeypatch.setenv('COORDINATOR_URL', 'https://x" onload="alert(1)')
    page = TestClient(dashboard_app).get('/').text
    assert 'onload="alert(1)"' not in page
