# GemmaNet

**The Open Network for AI Services**

[![PyPI version](https://img.shields.io/pypi/v/gemmanet)](https://pypi.org/project/gemmanet/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/gemmanet/gemmanet/test.yml?label=tests)](https://github.com/gemmanet/gemmanet/actions)

GemmaNet is a model-agnostic platform that turns any AI model into a node in
an open network of AI services. Provide a service in about 10 lines of code,
consume it in 5. Whether you're running Gemma, Llama, Qwen, Mistral, or your
own fine-tuned model, GemmaNet handles discovery, routing, load balancing and
reputation so you can focus on building great AI applications.

## Features

- **Model Agnostic** — Use any AI model: Gemma, Llama, Qwen, Mistral, or your own
- **Provide in 10 Lines** — Register a node and start serving requests with minimal code
- **Consume in 5 Lines** — Send tasks to the network and get results instantly
- **Reputation-Based Routing** — Requests go to the best node by reputation, load and measured speed
- **Real Streaming** — Tokens reach the caller as the node generates them
- **OpenAI Compatible** — Point the OpenAI SDK (or LangChain) at GemmaNet and it just works
- **Task Splitting** — Long translations are split across several nodes in parallel
- **Dashboard** — Real-time web dashboard for nodes, reputation and quick tests
- **Open Source** — Apache 2.0 licensed, community-driven development

## Quick Start

### Installation

```bash
pip install gemmanet             # SDK: Node + Client
pip install "gemmanet[server]"   # coordinator (needs PostgreSQL + Redis)
```

### Run a Coordinator

```bash
export DATABASE_URL=postgresql://user:pass@localhost:5432/gemmanet
export REDIS_URL=redis://localhost:6379/0
uvicorn gemmanet.coordinator.server:app --port 8800
```

Run a single coordinator process (no `--workers`); see
[Architecture](docs/architecture.md#deployment). For a production server,
`docker compose up -d` runs the whole stack (PostgreSQL, Redis, coordinator,
Caddy with the website and docs): follow [deploy/DEPLOY.md](deploy/DEPLOY.md).
Upgrading from 0.1? Follow [deploy/UPGRADE.md](deploy/UPGRADE.md).

### Get an API Key

Clients and nodes both authenticate with an API key:

```bash
curl -X POST http://localhost:8800/api/v1/register
# {"api_key": "gn_...", "account_id": "..."}
```

### Create a Node (Provider)

```python
from gemmanet import Node

def echo(content: str, **params) -> str:
    return f"Echo: {content}"

node = Node(
    name="my-echo-node",
    capabilities=["echo"],
    api_key="gn_your_api_key",
    coordinator_url="ws://localhost:8800/ws/node",
)
node.register_handler("echo", echo)
node.start()
```

A node's identity comes from its API key and name, so its reputation carries
over when it restarts. Handlers can be plain functions, `async` functions or
generators (which stream their output).

### Send a Task (Client)

```python
from gemmanet import Client

client = Client(api_key="gn_your_api_key", coordinator_url="http://localhost:8800")
result = client.request("echo", "Hello, GemmaNet!")
print(result.result)  # Echo: Hello, GemmaNet!
```

### Use the OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8800/v1", api_key="gn_your_api_key")
reply = client.chat.completions.create(
    model="gemmanet/auto",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(reply.choices[0].message.content)
```

## Documentation

- [Quick Start](docs/quickstart.md)
- [API Reference](docs/api_reference.md)
- [Architecture Overview](docs/architecture.md)
- [Examples](docs/examples.md)
- [Website](https://www.gemmanet.net)

## How It Works

1. **Create Nodes** — Register AI models as nodes with declared capabilities
2. **Connect** — Nodes connect to the GemmaNet coordinator via WebSocket and authenticate with their API key
3. **Route** — The coordinator routes each task to the best available node
4. **Build Reputation** — Completion rate, speed, uptime and user ratings shape each node's reputation, which steers future routing

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

# Run tests (needs PostgreSQL and Redis; see .github/workflows/test.yml)
export DATABASE_URL=postgresql://gemmanet:gemmanet@localhost:5432/gemmanet_test
pytest tests/ -v

# Lint
ruff check src/ tests/
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
