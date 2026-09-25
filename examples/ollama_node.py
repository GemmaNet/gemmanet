'''GemmaNet node powered by Ollama.

Prerequisites:
1. Install Ollama: https://ollama.com/download
2. Pull a model: ollama pull gemma2:9b
3. Get a GemmaNet API key: POST /api/v1/register
4. Run this script: GEMMANET_API_KEY=gn_... python examples/ollama_node.py

Your node will join the GemmaNet network and start
processing requests using your local Ollama model.
'''
import logging
import sys

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

from gemmanet import Node  # noqa: E402
from gemmanet.integrations.ollama import (  # noqa: E402
    OllamaHandler,
    OllamaSummarizeHandler,
    OllamaTranslateHandler,
)

# Create handlers for different capabilities
chat_handler = OllamaHandler(model='gemma2:9b')
translate_handler = OllamaTranslateHandler(model='gemma2:9b')
summarize_handler = OllamaSummarizeHandler(model='gemma2:9b')

# Check Ollama connection
if not chat_handler.check_connection():
    print('ERROR: Cannot connect to Ollama.')
    print('Make sure Ollama is running: ollama serve')
    print('And you have a model: ollama pull gemma2:9b')
    sys.exit(1)

print(f'Available Ollama models: {chat_handler.list_models()}')

# Create node with multiple capabilities
node = Node(
    name='ollama-powered-node',
    capabilities=['chat', 'translate', 'summarize'],
    languages=['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de'],
    model_info={'backend': 'ollama', 'model': 'gemma2:9b'},
)  # api_key is read from GEMMANET_API_KEY
node.register_handler('chat', chat_handler)
node.register_handler('translate', translate_handler)
node.register_handler('summarize', summarize_handler)

print('Starting node... (Ctrl+C to stop)')
node.start()
