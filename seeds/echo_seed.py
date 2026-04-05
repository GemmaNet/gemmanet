import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [ECHO-SEED] %(levelname)s: %(message)s')
from gemmanet import Node

def echo_handler(content, **params):
    prefix = params.get('prefix', 'Echo')
    return f'{prefix}: {content}'

node = Node(
    name='gemmanet-echo-seed',
    capabilities=['echo'],
    languages=['en'],
    coordinator_url=os.getenv('GEMMANET_COORDINATOR', 'ws://localhost:8800/ws/node'),
)
node.register_handler('echo', echo_handler)
node.start()
