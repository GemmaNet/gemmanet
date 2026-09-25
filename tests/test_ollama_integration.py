import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from gemmanet.integrations.ollama import (
    OllamaCodeHandler,
    OllamaHandler,
    OllamaTranslateHandler,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope='module')
def ollama_url():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, 'tests/mock_ollama.py', str(port)], cwd=ROOT)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                httpx.get(f'{url}/api/tags', timeout=0.5)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_ollama_handler_with_mock(ollama_url):
    handler = OllamaHandler(model='gemma2:9b', ollama_url=ollama_url)
    assert handler.check_connection() is True
    assert 'gemma2:9b' in handler.list_models()

    result = handler('Hello world')
    assert 'MockOllama:gemma2:9b' in result
    assert 'Hello world' in result
    assert result.usage['prompt_tokens'] == 7

    th = OllamaTranslateHandler(model='gemma2:9b', ollama_url=ollama_url)
    assert 'Translate the following text from en to ja' in th(
        'Hello', source_lang='en', target_lang='ja')
    handler.close()
    th.close()


def test_multi_turn_messages_are_forwarded(ollama_url):
    handler = OllamaHandler(ollama_url=ollama_url, system_prompt='be brief')
    messages = [
        {'role': 'user', 'content': 'first question'},
        {'role': 'assistant', 'content': 'first answer'},
        {'role': 'user', 'content': 'follow-up'},
    ]
    result = handler('follow-up', messages=messages)
    # system prompt + 3 conversation turns reach the model
    assert 'turns=4' in result
    assert 'follow-up' in result


def test_specialized_handlers_ignore_chat_history(ollama_url):
    handler = OllamaTranslateHandler(ollama_url=ollama_url)
    result = handler('Hi', messages=[{'role': 'user', 'content': 'x'}] * 3)
    assert 'turns=1' in result


def test_callers_cannot_switch_models_by_default(ollama_url):
    handler = OllamaHandler(model='gemma2:9b', ollama_url=ollama_url)
    assert 'MockOllama:gemma2:9b' in handler('hi', model='llama3:8b')
    permissive = OllamaHandler(model='gemma2:9b', ollama_url=ollama_url,
                               allow_model_override=True)
    assert 'MockOllama:llama3:8b' in permissive('hi', model='llama3:8b')


def test_stream_yields_pieces_and_returns_usage(ollama_url):
    handler = OllamaCodeHandler(ollama_url=ollama_url)
    gen = handler.stream('sort a list')
    pieces = []
    try:
        while True:
            pieces.append(next(gen))
    except StopIteration as stop:
        usage = stop.value
    assert len(pieces) > 3
    assert 'MockOllama:codellama:7b' in ''.join(pieces)
    assert usage['prompt_tokens'] == 7


def test_unreachable_ollama_raises_instead_of_returning_text():
    handler = OllamaHandler(ollama_url='http://127.0.0.1:9')
    with pytest.raises(RuntimeError, match='Cannot connect to Ollama'):
        handler('hi')
