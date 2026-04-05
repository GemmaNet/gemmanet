import subprocess, sys, time, os
import pytest


def test_ollama_handler_with_mock():
    # Start mock Ollama server
    mock_proc = subprocess.Popen(
        [sys.executable, 'tests/mock_ollama.py'],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    time.sleep(2)

    try:
        from gemmanet.integrations.ollama import OllamaHandler
        handler = OllamaHandler(model='gemma2:9b')

        # Check connection
        assert handler.check_connection() is True

        # List models
        models = handler.list_models()
        assert 'gemma2:9b' in models

        # Call handler
        result = handler('Hello world')
        assert 'MockOllama' in result
        assert 'Hello world' in result

        # Test translate handler
        from gemmanet.integrations.ollama import OllamaTranslateHandler
        th = OllamaTranslateHandler(model='gemma2:9b')
        result = th('Hello', source_lang='en', target_lang='zh')
        assert len(result) > 0

        handler.close()
        th.close()
    finally:
        mock_proc.terminate()
        mock_proc.wait(timeout=5)
