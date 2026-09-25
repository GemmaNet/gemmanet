"""Minimal GemmaNet node.

Get an API key first (POST /api/v1/register), then:
    GEMMANET_API_KEY=gn_... python examples/echo_node.py
"""
import logging

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
from gemmanet import Node  # noqa: E402


def echo_handler(content: str, **params) -> str:
    prefix = params.get('prefix', 'Echo')
    return f'{prefix}: {content}'


node = Node(
    name='echo-node',
    capabilities=['echo'],
    languages=['en'],
)  # api_key is read from GEMMANET_API_KEY
node.register_handler('echo', echo_handler)
print('Starting echo node... (Ctrl+C to stop)')
node.start()
