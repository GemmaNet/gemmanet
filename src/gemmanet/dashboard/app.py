"""FastAPI dashboard app (Jinja2 HTML)."""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gemmanet import __version__

TEMPLATE_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'

dashboard_app = FastAPI(title='GemmaNet Dashboard', version=__version__)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

if STATIC_DIR.exists():
    dashboard_app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')


@dashboard_app.get('/')
async def index(request: Request):
    coordinator_url = os.getenv('COORDINATOR_URL', 'http://localhost:8800')
    return templates.TemplateResponse(
        request, 'index.html',
        context={'coordinator_url': coordinator_url, 'version': __version__},
    )
