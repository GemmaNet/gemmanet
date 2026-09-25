# GemmaNet Architecture

## Design Philosophy

GemmaNet is a **platform-only** system. It does not run AI models itself — it provides the infrastructure for developers to connect their models (as nodes) with consumers (as clients). The platform handles authentication, routing, load balancing, content splitting and node reputation.

## Component Overview

```
┌─────────────────────────────────────────────────────┐
│                     Clients                         │
│       (SDK Client class / OpenAI SDK / Dashboard)   │
│         HTTP requests to /api/v1/* and /v1/*        │
└───────────────────┬─────────────────────────────────┘
                    │ HTTP (+ Server-Sent Events)
                    ▼
┌─────────────────────────────────────────────────────┐
│                  Coordinator                        │
│              (FastAPI Server)                       │
│                                                     │
│  ┌─────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │ Router  │  │ Registry │  │ Reputation        │   │
│  │ Engine  │  │ (Redis)  │  │ (Redis)           │   │
│  └─────────┘  └──────────┘  └───────────────────┘   │
│  ┌──────────────────────┐  ┌────────────────────┐   │
│  │ API keys (PostgreSQL)│  │ Task tracker       │   │
│  └──────────────────────┘  └────────────────────┘   │
│  ┌─────────────────────────────────────────────┐    │
│  │         WebSocket Manager                   │    │
│  └─────────────────────────────────────────────┘    │
└───────────────────┬─────────────────────────────────┘
                    │ WebSocket (node-initiated)
                    ▼
┌─────────────────────────────────────────────────────┐
│                     Nodes                           │
│              (SDK Node class)                       │
│     Developer-registered handlers per task type     │
└─────────────────────────────────────────────────────┘
```

## Components

### SDK Layer

- **Node**: Connects to the coordinator via WebSocket and authenticates with an API key. Registers capabilities (e.g., `translate`, `echo`) and languages, receives task assignments and returns results. Developers provide a handler per task type; plain functions run in a worker thread so a slow model never blocks heartbeats or other tasks, `async` handlers run on the event loop, and generator handlers stream their output.
- **Client**: Sends HTTP requests to the coordinator: submit tasks (optionally streaming), rate results, list nodes and check network status.

### Coordinator

The central server that orchestrates the network:

- **FastAPI Server**: REST endpoints for clients, an OpenAI-compatible API, the WebSocket endpoint for nodes, the dashboard and the forum.
- **API keys (PostgreSQL)**: Accounts are API keys (stored as SHA-256 hashes). Clients and nodes both use them.
- **WebSocket Manager**: Holds the live connection of each online node. If the same node identity connects twice, the newer connection replaces the older one.
- **Node Registry (Redis)**: Tracks online nodes with their capabilities, languages and load. Entries expire after 120 seconds without a heartbeat.
- **Task Tracker**: Matches results (and streamed chunks) arriving on node connections with the waiting requests. A node can only answer tasks sent on its own connection, and a disconnecting node fails its in-flight tasks immediately instead of letting them time out.
- **Routing Engine**: Scores the online, non-suspended nodes that offer the requested capability (see below) and decides when to split long content.
- **Reputation System (Redis)**: Scores each node from its task history and user ratings, and suspends consistently failing nodes.

### Dashboard

Web UI mounted at `/dashboard/` on the coordinator. Shows network status, online nodes with reputation and benchmark data, the reputation leaderboard and a quick test form. Built with Jinja2 templates and vanilla JavaScript; everything nodes report is HTML-escaped before display.

## Node Identity

A node authenticates with an API key in its `node_register` message. The coordinator derives the node id from the key's account and the node's name (UUIDv5), so:

- a node keeps its id — and its reputation — across restarts;
- nobody without the API key can claim that id;
- one account can run many nodes under different names.

## Routing

For each candidate node the router computes:

```
score = 0.25 × capability match (always 1 for candidates)
      + 0.25 × reputation / 100
      + 0.15 × (1 − CPU load)
      + 0.10 × historical speed        (from completed tasks)
      + 0.15 × benchmark speed         (timed by the coordinator)
      + 0.10 × random                  (spreads load)
      − 0.20 if the last benchmark failed
```

and sends the task to the highest-scoring node. If sending fails, it retries on the next best node (up to 3 attempts).

**Benchmarks**: When a node connects (and every 6 hours) the coordinator sends three prompts and times the round trip itself; the node's self-reported timings are not trusted for routing.

**Splitting**: Content longer than 1,000 characters is split into 3 chunks (by paragraph, sentence, or characters) and processed in parallel — but only for task types whose chunks can be handled independently. By default that is `translate`; set `GEMMANET_SPLIT_TASKS` (comma-separated) to change it. Chat, summarization and similar tasks are never split.

## Reputation

```
score = 40 × completion rate + 20 × speed + 20 × recent activity + 20 × user rating
```

- **Completion rate**: tasks the node completed / tasks it was given. A task counts as failed when the handler raised an error, the node disconnected, or it timed out.
- **Speed**: average response time, 1.0 under 1 s down to 0 at 10 s.
- **Recent activity**: decays over the 24 hours since the node's last task.
- **User rating**: average of the last 100 ratings (1-5 stars, neutral when unrated). Only the account that requested a task can rate it, once.

New nodes start at 50. A node below 35 after more than 10 tasks is suspended from routing for 24 hours.

## Message Flow

### Standard Request

```
Client                    Coordinator                  Node
  │                           │                          │
  │  POST /api/v1/request     │                          │
  │ ────────────────────────► │                          │
  │                           │  validate API key        │
  │                           │  find best node          │
  │                           │                          │
  │                           │  task_assign (WebSocket) │
  │                           │ ────────────────────────►│
  │                           │                          │ handler()
  │                           │  task_result (WebSocket) │
  │                           │ ◄────────────────────────│
  │                           │  update reputation       │
  │  TaskResult (HTTP)        │                          │
  │ ◄──────────────────────── │                          │
```

### Streaming Request

With `"stream": true` (or `stream=True` on the OpenAI API) the task is assigned with `stream: true`; the node sends `task_chunk` messages as its handler yields text, and the coordinator relays each one to the client as a Server-Sent Event before the final `task_result`.

### Split Request (long `translate` content)

```
Client                    Coordinator              Node A    Node B
  │                           │                      │         │
  │  POST /api/v1/request     │                      │         │
  │ ────────────────────────► │                      │         │
  │                           │  split into chunks   │         │
  │                           │  task_assign chunk1  │         │
  │                           │ ────────────────────►│         │
  │                           │  task_assign chunk2  │         │
  │                           │ ──────────────────────────────►│
  │                           │  task_result chunk1  │         │
  │                           │ ◄────────────────────│         │
  │                           │  task_result chunk2  │         │
  │                           │ ◄──────────────────────────────│
  │                           │  merge results       │         │
  │                           │  update reputation   │         │
  │  TaskResult (HTTP)        │                      │         │
  │ ◄──────────────────────── │                      │         │
```

## Deployment

Run **exactly one coordinator process**. Node connections and in-flight tasks live in that process's memory, so `uvicorn --workers N` or several coordinators sharing a Redis would lose tasks. The coordinator enforces this with a lock in Redis: a second instance waits up to 20 s (`GEMMANET_INSTANCE_LOCK_WAIT`) for the lock and then refuses to start. Cross-process dispatch is planned in [the multi-instance design](design/multi-instance.md).

## How Developers Extend

To add a new AI capability to the network:

```python
from gemmanet import Node

def summarize(content: str, **params) -> str:
    # Call your model, API, or processing logic here
    return my_model.summarize(content)

def classify(content: str, **params) -> str:
    return my_model.classify(content)

node = Node(
    name='my-model-node',
    capabilities=['summarize', 'classify'],
    languages=['en', 'zh'],
    api_key='gn_your_api_key',
)
node.register_handler('summarize', summarize)
node.register_handler('classify', classify)
node.start()
```

A handler takes `content` (str) and `**params` (keyword arguments from the request) and returns a string — or yields strings to stream. Return `gemmanet.Completion(text, usage={...})` to report token usage. This makes it easy to wrap any model — local transformers, hosted APIs, custom pipelines, or simple rule-based processors.
