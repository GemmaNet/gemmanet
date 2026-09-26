"""The Cloudflare Pages build: website + docs, pointed at the API origin."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('build_pages', ROOT / 'scripts' / 'build_pages.py')
build_pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_pages)


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    return build_pages.build(tmp_path_factory.mktemp('pages') / 'dist', 'https://api.example.test/')


def test_website_links_point_to_the_api(site):
    html = (site / 'index.html').read_text()
    for path in ('href="/talk/"', 'href="/dashboard/"', "fetch('/talk/api/recent')"):
        assert path not in html
    assert 'href="https://api.example.test/talk/"' in html
    assert 'href="https://api.example.test/dashboard/"' in html
    assert "fetch('https://api.example.test/talk/api/recent')" in html
    assert 'href="/docs/' in html  # docs stay on Pages


def test_docs_are_built(site):
    assert 'GemmaNet' in (site / 'docs' / 'index.html').read_text()
    assert (site / 'docs' / 'quickstart' / 'index.html').exists()


def test_old_paths_redirect_to_the_api(site):
    rules = (site / '_redirects').read_text().splitlines()
    assert '/talk/* https://api.example.test/talk/:splat 308' in rules
    assert '/dashboard https://api.example.test/dashboard/ 308' in rules
    assert '/v1/* https://api.example.test/v1/:splat 308' in rules


def test_build_fails_if_the_website_drops_a_rewritten_link(monkeypatch, tmp_path):
    monkeypatch.setattr(build_pages, 'website_rewrites',
                        lambda api: [('href="/not-on-the-page/"', 'x')])
    with pytest.raises(SystemExit, match='no longer contains'):
        build_pages.build(tmp_path / 'dist')
