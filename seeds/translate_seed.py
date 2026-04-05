import logging, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [TRANSLATE-SEED] %(levelname)s: %(message)s')
from gemmanet import Node

def translate_handler(content, source_lang='en', target_lang='zh', **kw):
    # Simple mock that shows the system works
    # In production, real users will run Ollama-powered nodes
    result = f'[Translated {source_lang}->{target_lang}] {content}'
    return result

def summarize_handler(content, max_words=50, **kw):
    words = content.split()
    if len(words) <= max_words:
        return content
    return ' '.join(words[:max_words]) + '...'

node = Node(
    name='gemmanet-translate-seed',
    capabilities=['translate', 'summarize'],
    languages=['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de'],
    coordinator_url=os.getenv('GEMMANET_COORDINATOR', 'ws://localhost:8800/ws/node'),
)
node.register_handler('translate', translate_handler)
node.register_handler('summarize', summarize_handler)
node.start()
