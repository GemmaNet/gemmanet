# GemmaNet Quick Start Guide

## What is GemmaNet

GemmaNet is a decentralized platform that connects AI model providers with consumers through a credit-based economy. Developers register their models as nodes on the network, and clients send task requests that get routed to the best available node. The platform handles routing, load balancing, content splitting, and payments automatically.

## Install

From source:

```bash
cd /home/cxwg0011/gemmanet
pip install -e .
```

Or from PyPI (when published):

```bash
pip install gemmanet
```

## Start the Coordinator

The coordinator is the central hub that routes requests between clients and nodes.

```bash
cd /home/cxwg0011/gemmanet && source .venv/bin/activate
python -m uvicorn gemmanet.coordinator.server:app --host 0.0.0.0 --port 8800
```

The coordinator will start on port 8800 with the dashboard available at `http://localhost:8800/dashboard/`.

## Create a Node

Nodes provide AI services on the network. Here's a simple echo node:

```python
from gemmanet import Node

def echo_handler(content: str, **params) -> str:
    prefix = params.get('prefix', 'Echo')
    return f'{prefix}: {content}'

node = Node(
    name='my-echo-node',
    capabilities=['echo'],
    languages=['en'],
)
node.register_handler('echo', echo_handler)
node.start()  # Connects to coordinator via WebSocket
```

## Use the Client

Clients consume AI services by sending task requests:

```python
from gemmanet import Client

client = Client(api_key='my-api-key')
result = client.request(task='echo', content='Hello GemmaNet!')
print(result.result)   # "Echo: Hello GemmaNet!"
print(result.cost)     # 10 credits
client.close()
```

## Check the Dashboard

Open your browser to `http://localhost:8800/dashboard/` to see:

- Network status and online nodes
- Submit test requests via the Quick Test form
- Monitor your credit balance and transaction history

## Run the Demo

The translation demo starts a coordinator, 3 specialized nodes, and sends requests:

```bash
python examples/demo_translate_app.py
```

## Next Steps

- **Plug in your own model**: Replace the handler function with calls to your ML model, LLM API, or any processing logic.
- **Add capabilities**: Register multiple handlers on a single node for different task types.
- **Deploy to cloud**: Run the coordinator on a server and nodes on GPU instances. Connect them by setting `coordinator_url` on each node.
- **Scale horizontally**: Add more nodes with the same capabilities for automatic load balancing.
- **Use the API**: See the full API reference at `http://localhost:8800/docs` or in `docs/api_reference.md`.
