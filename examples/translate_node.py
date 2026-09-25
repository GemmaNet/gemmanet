"""Translation node with a mock model. Run with GEMMANET_API_KEY set."""
import logging

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
from gemmanet import Node  # noqa: E402


def translate_handler(content: str, source_lang: str = 'en',
                      target_lang: str = 'zh', **kwargs) -> str:
    # MOCK: Replace with real model inference
    # e.g. result = my_gemma4_model.generate(...)
    return f'[{source_lang}->{target_lang}] {content}'


node = Node(
    name='translate-demo',
    capabilities=['translate'],
    languages=['en', 'zh', 'ja'],
    model_info={'model': 'mock-translator', 'type': 'demo'},
)  # api_key is read from GEMMANET_API_KEY
node.register_handler('translate', translate_handler)
print('Starting translation node... (Ctrl+C to stop)')
node.start()
