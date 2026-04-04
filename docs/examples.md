# Examples

## Echo Node (Simplest Possible)

The echo node is the "Hello World" of GemmaNet. It registers a single capability
and echoes back whatever it receives.

```python
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
```

**What's happening:**

1. Create a handler function that takes `content` and optional `**params`
2. Create a `Node` with a name and list of capabilities
3. Register the handler for the `echo` capability
4. Call `node.start()` to connect to the coordinator and begin processing requests

## Translation Node

A more realistic example that simulates a translation service. In production,
you'd replace the mock with a real model (Gemma, Llama, etc.).

```python
import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
from gemmanet import Node

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
)
node.register_handler('translate', translate_handler)
print(f'Translation node created: {node.node_id}')
print('Starting node... (Ctrl+C to stop)')
node.start()
```

**Key differences from the echo node:**

- Declares multiple `languages` the node supports
- Provides `model_info` metadata so the coordinator knows what model is running
- Handler accepts typed parameters (`source_lang`, `target_lang`) alongside `**kwargs`

## Client Usage

The client SDK lets you consume services from any node on the network.

```python
from gemmanet import Client

# Create client with your API key
client = Client(api_key='demo-key-001')

# Check network status
print('Network status:', client.network_status())

# List available nodes
nodes = client.nodes()
print(f'Online nodes: {len(nodes)}')
for n in nodes:
    print(f'  - {n.name}: {n.capabilities}')

# Send a request
result = client.request(
    task='echo',
    content='Hello GemmaNet!',
    params={'prefix': 'Test'},
)
print(f'Result: {result.result}')
print(f'Cost: {result.cost} credits')
print(f'Node: {result.node_id}')

# Check balance
print(f'Balance: {client.balance()} credits')

client.close()
```

**What's happening:**

1. Create a `Client` with your API key
2. Use `client.nodes()` to discover available services
3. Use `client.request()` to send work to the network - the coordinator picks the best node
4. The response includes the result, cost in credits, and which node handled it

## Multi-Node Demo

The `demo_translate_app.py` script demonstrates a full GemmaNet deployment:

1. **Starts the coordinator** on port 8800
2. **Launches 3 translation nodes**, each with different language specialties:
    - Node 1: English-Chinese specialist
    - Node 2: English-Japanese specialist
    - Node 3: General translator (EN, ZH, JA, KO) + echo capability
3. **Sends requests** through the client SDK and shows routing in action
4. **Tests long text splitting** across multiple nodes for parallel processing

Run it with:

```bash
cd /home/cxwg0011/gemmanet
source .venv/bin/activate
python examples/demo_translate_app.py
```

The demo also starts the dashboard at `http://localhost:8800/dashboard/` where you
can see nodes, transactions, and network status in real time.
