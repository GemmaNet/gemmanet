# GemmaNet Architecture

## Design Philosophy

GemmaNet is a **platform-only** system. It does not run AI models itself — it provides the infrastructure for developers to connect their models (as nodes) with consumers (as clients). The platform handles routing, load balancing, content splitting, and credit-based payments.

## Component Overview

```
┌─────────────────────────────────────────────────────┐
│                     Clients                         │
│              (SDK Client class)                     │
│         HTTP requests to /api/v1/*                  │
└───────────────────┬─────────────────────────────────┘
                    │ HTTP
                    ▼
┌─────────────────────────────────────────────────────┐
│                  Coordinator                        │
│              (FastAPI Server)                       │
│                                                     │
│  ┌─────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Router  │  │ Registry │  │ Credit Service    │  │
│  │ Engine  │  │ (Redis)  │  │ (PostgreSQL)      │  │
│  └─────────┘  └──────────┘  └───────────────────┘  │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │         WebSocket Manager                   │    │
│  └─────────────────────────────────────────────┘    │
└───────────────────┬─────────────────────────────────┘
                    │ WebSocket
                    ▼
┌─────────────────────────────────────────────────────┐
│                     Nodes                           │
│              (SDK Node class)                       │
│     Developer-registered handlers per task type     │
└─────────────────────────────────────────────────────┘
```

## Components

### SDK Layer

- **Node**: Connects to the coordinator via WebSocket. Registers capabilities (e.g., `translate`, `echo`) and languages. Receives task assignments and returns results. Developers provide handler functions for each task type.
- **Client**: Sends HTTP requests to the coordinator. Submits tasks, checks balance, and views transaction history. Handles authentication via API keys.

### Coordinator

The central server that orchestrates the network:

- **FastAPI Server**: REST endpoints for clients, WebSocket endpoint for nodes, and the developer dashboard.
- **WebSocket Manager**: Manages persistent connections to all online nodes. Handles connect/disconnect, message routing, and broadcasts.
- **Node Registry (Redis)**: Tracks online nodes with their capabilities, languages, and load info. Entries expire after 120 seconds without a heartbeat.
- **Routing Engine**: Scores available nodes based on capability match (50%), inverse load (30%), and randomness (20%). Determines when to split large content across multiple nodes.
- **Credit Service (PostgreSQL)**: Manages accounts, balances, charges, rewards, and transaction history.

### Dashboard

Web UI mounted at `/dashboard/` on the coordinator. Shows network status, online nodes, a quick test form, and transaction history. Built with Jinja2 templates and vanilla JavaScript.

## Message Flow

### Standard Request

```
Client                    Coordinator                  Node
  │                           │                          │
  │  POST /api/v1/request     │                          │
  │ ────────────────────────► │                          │
  │                           │  check credits           │
  │                           │  find best node          │
  │                           │  charge client           │
  │                           │                          │
  │                           │  TASK_ASSIGN (WebSocket) │
  │                           │ ────────────────────────►│
  │                           │                          │ handler()
  │                           │  TASK_RESULT (WebSocket) │
  │                           │ ◄────────────────────────│
  │                           │  reward node             │
  │  TaskResult (HTTP)        │                          │
  │ ◄──────────────────────── │                          │
```

### Split Request (content > 1000 chars)

```
Client                    Coordinator              Node A    Node B
  │                           │                      │         │
  │  POST /api/v1/request     │                      │         │
  │ ────────────────────────► │                      │         │
  │                           │  split into chunks   │         │
  │                           │  charge client       │         │
  │                           │                      │         │
  │                           │  TASK_ASSIGN chunk1  │         │
  │                           │ ────────────────────►│         │
  │                           │  TASK_ASSIGN chunk2  │         │
  │                           │ ──────────────────────────────►│
  │                           │                      │         │
  │                           │  TASK_RESULT chunk1  │         │
  │                           │ ◄────────────────────│         │
  │                           │  TASK_RESULT chunk2  │         │
  │                           │ ◄──────────────────────────────│
  │                           │  merge results       │         │
  │                           │  reward nodes        │         │
  │  TaskResult (HTTP)        │                      │         │
  │ ◄──────────────────────── │                      │         │
```

## Credit System

Every account starts with **1000 credits**. The credit flow for each task:

1. **Charge**: Client is charged `10 × num_chunks` credits when a request is submitted.
2. **Freeze** (implicit): Credits are held while the task is processing.
3. **Reward**: Each node that processes a chunk receives 10 credits.
4. **Refund**: If a task times out or fails, credits are returned to the client.

Transaction types: `charge` (debit from client), `reward` (credit to node), `refund` (credit back to client on failure).

## How Developers Extend

To add a new AI capability to the network:

```python
from gemmanet import Node

def my_handler(content: str, **params) -> str:
    # Call your model, API, or processing logic here
    result = my_model.predict(content)
    return result

node = Node(
    name='my-model-node',
    capabilities=['summarize', 'classify'],
    languages=['en', 'zh'],
)
node.register_handler('summarize', my_handler)
node.register_handler('classify', another_handler)
node.start()
```

The `register_handler` function accepts any callable that takes `content` (str) and `**params` (keyword arguments from the request) and returns a string result. This makes it easy to wrap any model — local transformers, OpenAI API calls, custom pipelines, or simple rule-based processors.
