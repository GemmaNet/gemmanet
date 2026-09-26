"""Build the static site for Cloudflare Pages: the website and the docs.

Cloudflare Pages project settings:
    Build command:          pip install -r docs/requirements.txt && python scripts/build_pages.py
    Build output directory: pages-dist
    Environment variables:  PYTHON_VERSION=3.11
                            GEMMANET_API_ORIGIN=https://api.gemmanet.net  (the default)

The dynamic parts (API, WebSocket, dashboard, forum) run on the coordinator
at GEMMANET_API_ORIGIN, so the website's links and its forum preview are
pointed there, and old paths on the main domain redirect to it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_ORIGIN = 'https://api.gemmanet.net'

# Paths that live on the coordinator, not on Pages.
COORDINATOR_PATHS = ('/talk', '/dashboard', '/api', '/v1')


def website_rewrites(api: str) -> list[tuple[str, str]]:
    return [
        ('href="/talk/"', f'href="{api}/talk/"'),
        ('href="/dashboard/"', f'href="{api}/dashboard/"'),
        ("fetch('/talk/api/recent')", f"fetch('{api}/talk/api/recent')"),
    ]


def redirects(api: str) -> str:
    lines = []
    for path in COORDINATOR_PATHS:
        # 308 keeps the method, so a mistaken POST to the main domain still
        # reaches the API when the client follows redirects.
        lines.append(f'{path} {api}{path}/ 308')
        lines.append(f'{path}/* {api}{path}/:splat 308')
    return '\n'.join(lines) + '\n'


def build(out: Path, api_origin: str = DEFAULT_API_ORIGIN) -> Path:
    api = api_origin.rstrip('/')
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / 'website', out)

    index = out / 'index.html'
    html = index.read_text()
    for old, new in website_rewrites(api):
        if old not in html:
            # The website changed: fail loudly instead of shipping a page
            # whose links point at paths Pages doesn't serve.
            raise SystemExit(f'website/index.html no longer contains {old!r}; '
                             'update website_rewrites() in scripts/build_pages.py')
        html = html.replace(old, new)
    index.write_text(html)

    (out / '_redirects').write_text(redirects(api))

    subprocess.run([sys.executable, '-m', 'mkdocs', 'build', '--strict',
                    '--config-file', str(ROOT / 'mkdocs.yml'),
                    '--site-dir', str(out / 'docs')], check=True)
    return out


if __name__ == '__main__':
    target = build(ROOT / 'pages-dist', os.getenv('GEMMANET_API_ORIGIN', DEFAULT_API_ORIGIN))
    print(f'Built {target}')
