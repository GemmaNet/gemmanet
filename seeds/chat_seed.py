import logging, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [CHAT-SEED] %(levelname)s: %(message)s')
from gemmanet import Node

def chat_handler(content, **params):
    content_lower = content.lower().strip()
    if any(g in content_lower for g in ['hello', 'hi', 'hey']):
        return 'Hello! I am a GemmaNet seed node. This is a demo response. '\
               'To get real AI responses, connect a node with Ollama or another model.'
    elif 'what is gemmanet' in content_lower:
        return 'GemmaNet is an open platform that lets developers turn any AI model '\
               'into a node in a global AI services network. Visit https://gemmanet.net'
    elif any(w in content_lower for w in ['help', 'how', 'what']):
        return f'You asked: {content}. This is a demo seed node with basic responses. '\
               'For real AI, developers connect models via the GemmaNet SDK. '\
               'See https://gemmanet.net/docs/quickstart/'
    else:
        return f'[GemmaNet Seed] Received: {content[:200]}. '\
               'This seed node provides basic responses. '\
               'Real AI-powered nodes can be connected using any model via the SDK.'

node = Node(
    name='gemmanet-chat-seed',
    capabilities=['chat'],
    languages=['en'],
    coordinator_url=os.getenv('GEMMANET_COORDINATOR', 'ws://localhost:8800/ws/node'),
)
node.register_handler('chat', chat_handler)
node.start()
