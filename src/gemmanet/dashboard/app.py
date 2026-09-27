"""FastAPI dashboard app (Jinja2 HTML)."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gemmanet import __version__

TEMPLATE_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'

dashboard_app = FastAPI(title='GemmaNet Dashboard', version=__version__)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def content_security_policy() -> str:
    """No inline scripts: the dashboard keeps the user's API key in localStorage.

    The page calls the API at COORDINATOR_URL, normally its own origin.
    """
    connect = ["'self'"]
    url = urlsplit(os.getenv('COORDINATOR_URL', 'http://localhost:8800'))
    if url.scheme in ('http', 'https') and url.netloc:
        connect.append(f'{url.scheme}://{url.netloc}')
    return ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"connect-src {' '.join(connect)}; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@dashboard_app.middleware('http')
async def add_csp(request: Request, call_next):
    response = await call_next(request)
    response.headers['Content-Security-Policy'] = content_security_policy()
    return response

if STATIC_DIR.exists():
    dashboard_app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')


@dashboard_app.get('/')
async def index(request: Request):
    coordinator_url = os.getenv('COORDINATOR_URL', 'http://localhost:8800')
    # The main website; its own origin when the site is on Cloudflare Pages.
    site_url = os.getenv('GEMMANET_SITE_URL', '/')
    docs_url = '/docs/' if site_url == '/' else site_url.rstrip('/') + '/docs/'
    return templates.TemplateResponse(
        request, 'index.html',
        context={'coordinator_url': coordinator_url, 'version': __version__,
                 'site_url': site_url, 'docs_url': docs_url},
    )
