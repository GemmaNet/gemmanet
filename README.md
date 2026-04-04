# GemmaNet

**The Operating System for the AI Economy**

[![PyPI version](https://img.shields.io/pypi/v/gemmanet)](https://pypi.org/project/gemmanet/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/gemmanet/gemmanet/test.yml?label=tests)](https://github.com/gemmanet/gemmanet/actions)

GemmaNet is a model-agnostic platform that turns any AI model into a node in a
global AI services network. Provide services in 10 lines of code, consume them
in 5. Whether you're running Gemma, Llama, Qwen, Mistral, or your own
fine-tuned model, GemmaNet handles discovery, routing, load balancing, and
credit transfers so you can focus on building great AI applications.

## Features

- **Model Agnostic** — Use any AI model: Gemma, Llama, Qwen, Mistral, or your own
- **Provide in 10 Lines** — Register a node and start serving requests with minimal code
- **Consume in 5 Lines** — Send tasks to the network and get results instantly
- **Earn Credits** — Serve AI requests from other users and earn credits automatically
- **Smart Routing** — Requests are routed to the best available node based on capability and load
- **Task Splitting** — Large tasks are automatically split across multiple nodes for parallel processing
- **Built-in Credits** — Integrated credit system for frictionless payments between nodes
- **Dashboard** — Real-time web dashboard for monitoring nodes, tasks, and credit balances
- **Open Source** — Apache 2.0 licensed, community-driven development

## Quick Start

### Installation

```bash
pip install gemmanet
```

### Create a Node (Provider)

```python
from gemmanet import Node

node = Node(
    name="my-echo-node",
    capabilities=["echo"],
    hub_url="ws://localhost:8000/ws/node",
)

@node.handler("echo")
async def handle_echo(task):
    return {"response": task.payload["text"]}

node.run()
```

### Send a Task (Client)

```python
from gemmanet import Client

client = Client(hub_url="http://localhost:8000")
result = client.send_task("echo", {"text": "Hello, GemmaNet!"})
print(result)
```

## Documentation

- [Architecture Overview](docs/architecture.md)
- [API Reference](docs/api.md)
- [Dashboard Guide](docs/dashboard.md)
- [Deployment Guide](docs/deployment.md)
- [Website](https://www.gemmanet.net)

## How It Works

1. **Create Nodes** — Register AI models as nodes with declared capabilities
2. **Connect** — Nodes connect to the GemmaNet hub via WebSocket
3. **Route** — The hub routes incoming tasks to the best available node
4. **Transfer Credits** — Credits are automatically transferred from consumers to providers

## Development

```bash
# Clone the repository
git clone https://github.com/gemmanet/gemmanet.git
cd gemmanet

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --ignore=tests/test_e2e.py

# Lint
ruff check src/
```

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for
guidelines on how to get started.

## License

GemmaNet is licensed under the [Apache License 2.0](LICENSE).

---

**Disclaimer:** Gemma is a trademark of Google LLC. GemmaNet is an independent
open-source project and is not affiliated with, endorsed by, or sponsored by
Google.
