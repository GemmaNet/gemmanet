# GemmaNet Quick Start Guide

## What is GemmaNet

GemmaNet is an open network of AI services. Developers register their models as nodes on the network, and clients send task requests that get routed to the best available node based on reputation, load and speed. The platform handles authentication, routing, load balancing, content splitting and streaming.

## Install

From source:

```bash
git clone https://github.com/gemmanet/gemmanet.git
cd gemmanet
pip install -e ".[server]"
```

Or from PyPI (when published):

```bash
pip install gemmanet             # SDK only (Node + Client)
pip install "gemmanet[server]"   # plus the coordinator
```

## Start the Coordinator

The coordinator is the central hub that routes requests between clients and nodes. It needs PostgreSQL (API keys) and Redis (node registry and reputation):

```bash
export DATABASE_URL=postgresql://user:pass@localhost:5432/gemmanet
export REDIS_URL=redis://localhost:6379/0
python -m uvicorn gemmanet.coordinator.server:app --host 0.0.0.0 --port 8800
```

The coordinator will start on port 8800 with the dashboard available at `http://localhost:8800/dashboard/`. Run a single process — don't pass `--workers`.

## Get an API Key

```bash
curl -X POST http://localhost:8800/api/v1/register
# {"api_key": "gn_...", "account_id": "..."}
```

Use the same key for your nodes and your clients, or register separate ones.

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
    api_key='gn_your_api_key',  # or set GEMMANET_API_KEY
)
node.register_handler('echo', echo_handler)
node.start()  # Connects to ws://localhost:8800/ws/node
```

The coordinator gives the node a stable id derived from your account and the node name, so its reputation is kept when you restart it.

## Use the Client

Clients consume AI services by sending task requests:

```python
from gemmanet import Client

client = Client(api_key='gn_your_api_key')
result = client.request(task='echo', content='Hello GemmaNet!')
print(result.result)   # "Echo: Hello GemmaNet!"
print(result.node_id)  # which node served it
client.rate(result.task_id, 5)  # optional: feeds the node's reputation
client.close()
```

## Stream Results

Handlers that `yield` text stream it to the caller as it is produced:

```python
def story(content: str, **params):
    for word in ['Once ', 'upon ', 'a ', 'time']:
        yield word

node.register_handler('story', story)
```

```python
for piece in client.request_stream('story', 'Tell me a story'):
    print(piece, end='', flush=True)
```

## Check the Dashboard

Open your browser to `http://localhost:8800/dashboard/` to see:

- Network status and online nodes
- Node reputation, benchmark speed and the leaderboard
- Submit test requests via the Quick Test form

## Run the Demo

The translation demo starts a coordinator, 3 specialized nodes, and sends requests:

```bash
python examples/demo_translate_app.py
```

## Use with OpenAI SDK

Any app using the OpenAI Python SDK can use GemmaNet by changing the base URL and API key:

```python
from openai import OpenAI
client = OpenAI(base_url='https://api.gemmanet.net/v1', api_key='gn_your_key')
response = client.chat.completions.create(
    model='gemmanet/auto',
    messages=[{'role': 'user', 'content': 'Hello, GemmaNet!'}],
)
print(response.choices[0].message.content)
```

The full conversation (system, user and assistant messages) is forwarded to the node, and `stream=True` streams tokens as they are generated.

## Use with Ollama

If you have Ollama installed locally, you can connect it to GemmaNet
in a few lines:

```python
from gemmanet import Node
from gemmanet.integrations.ollama import OllamaHandler

handler = OllamaHandler(model='gemma2:9b')  # or any Ollama model
node = Node(name='my-ollama-node', capabilities=['chat'], api_key='gn_your_api_key')
node.register_handler('chat', handler)
node.start()
```

`OllamaHandler` supports multi-turn conversations, streaming and token usage
reporting. Callers cannot switch your node to another local model unless you
pass `allow_model_override=True`.

Available specialized handlers:
- `OllamaHandler` - General chat/completion
- `OllamaTranslateHandler` - Translation with language params
- `OllamaSummarizeHandler` - Text summarization
- `OllamaCodeHandler` - Code generation (defaults to codellama)

## Next Steps

- **Plug in your own model**: Replace the handler function with calls to your ML model, LLM API, or any processing logic.
- **Add capabilities**: Register multiple handlers on a single node for different task types.
- **Deploy to cloud**: Run the coordinator on a server and nodes on GPU instances. Connect them by setting `coordinator_url` on each node.
- **Scale horizontally**: Add more nodes with the same capabilities for automatic load balancing.
- **Use the API**: See the full API reference at `http://localhost:8800/docs` or in `docs/api_reference.md`.
