import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
from gemmanet import Node

def echo_handler(content: str, **params) -> str:
    prefix = params.get('prefix', 'Echo')
    return f'{prefix}: {content}'

node = Node(
    name='echo-node',
    capabilities=['echo'],
    languages=['en'],
)
node.register_handler('echo', echo_handler)
print(f'Echo node created: {node.node_id}')
print('Starting node... (Ctrl+C to stop)')
node.start()
