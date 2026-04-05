# GemmaNet API Reference

Base URL: `http://localhost:8800`

## GET /api/v1/status

Get the current network status.

**Auth required:** No

**Response:**

```json
{
  "status": "running",
  "version": "0.1.0a1",
  "online_nodes": 3,
  "total_tasks_today": 42
}
```

---

## GET /api/v1/nodes

List online nodes, optionally filtered by capability.

**Auth required:** No

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `capability` | string (optional) | Filter nodes by capability (e.g., `translate`) |

**Response:**

```json
[
  {
    "node_id": "abc-123",
    "name": "zh-specialist",
    "capabilities": ["translate"],
    "languages": ["en", "zh"],
    "online": true,
    "load": 0.2
  }
]
```

---

## POST /api/v1/request

Submit a task request. The coordinator routes it to the best available node.

**Auth required:** No (API key passed in body)

**Request body:**

```json
{
  "task_type": "echo",
  "content": "Hello world!",
  "params": {},
  "max_cost": 50,
  "api_key": "your-api-key"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `task_type` | string | Task type to execute (e.g., `echo`, `translate`) |
| `content` | string | Content to process |
| `params` | object | Additional parameters passed to the node handler |
| `max_cost` | int (optional) | Maximum credits willing to spend |
| `api_key` | string | Client API key for authentication and billing |

**Response (200):**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "result": "Echo: Hello world!",
  "cost": 10,
  "node_id": "abc-123",
  "processing_time_ms": 15
}
```

**Errors:**

| Code | Detail |
|------|--------|
| 401 | Missing API key |
| 402 | Insufficient credits or cost exceeds max_cost |
| 404 | No node available for this task |
| 504 | Task timed out (60s) |

---

## GET /api/v1/balance

Get the credit balance for the authenticated account.

**Auth required:** Yes (`Authorization: Bearer <api_key>`)

**Response:**

```json
{
  "node_id": "your-api-key",
  "balance": 970,
  "total_earned": 0,
  "total_spent": 30
}
```

---

## GET /api/v1/history

Get transaction history for the authenticated account.

**Auth required:** Yes (`Authorization: Bearer <api_key>`)

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `limit` | int (default: 20) | Maximum number of transactions to return |

**Response:**

```json
[
  {
    "id": "tx-001",
    "tx_type": "charge",
    "amount": -10,
    "task_id": "550e8400-...",
    "timestamp": "2025-01-15T10:30:00Z"
  }
]
```

---

## WebSocket /ws/node

Node connection endpoint. Nodes connect here to register, receive tasks, and send results.

**Protocol:** WebSocket

**Connection flow:**

1. Node connects to `ws://localhost:8800/ws/node`
2. Node sends `NODE_REGISTER` message with its info
3. Coordinator sends `CREDIT_UPDATE` with initial balance
4. Node receives `TASK_ASSIGN` messages when tasks are routed to it
5. Node sends `TASK_RESULT` messages with completed results
6. Node sends periodic `HEARTBEAT` messages (every 30s)

**Message format:**

All messages are JSON with this structure:

```json
{
  "msg_id": "unique-id",
  "msg_type": "NODE_REGISTER",
  "payload": { ... },
  "sender_id": "node-id",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

**Message types:**

| Type | Direction | Description |
|------|-----------|-------------|
| `NODE_REGISTER` | Node -> Coordinator | Register node with capabilities |
| `HEARTBEAT` | Node -> Coordinator | Keep-alive with load info |
| `TASK_ASSIGN` | Coordinator -> Node | Assign a task for processing |
| `TASK_RESULT` | Node -> Coordinator | Return task result |
| `CREDIT_UPDATE` | Coordinator -> Node | Notify balance change |
| `ERROR` | Coordinator -> Node | Error notification |

---

## OpenAI Compatible API

GemmaNet provides an OpenAI-compatible endpoint. Any application
using the OpenAI SDK can switch to GemmaNet by changing two lines:

```python
from openai import OpenAI
client = OpenAI(
    base_url='https://api.gemmanet.net/v1',  # Change this
    api_key='gn_your_key',                     # Change this
)
# Everything else stays the same!
```

### Endpoints

#### POST /v1/chat/completions

Send a chat completion request in standard OpenAI format.

**Auth required:** Yes (`Authorization: Bearer <api_key>`)

**Request body:**

```json
{
  "model": "gemmanet/auto",
  "messages": [
    {"role": "system", "content": "You are a translator."},
    {"role": "user", "content": "Translate to Chinese: Hello world"}
  ],
  "max_tokens": 1024,
  "temperature": 0.7,
  "stream": false
}
```

**Response (200):**

```json
{
  "id": "chatcmpl-xxxxx",
  "object": "chat.completion",
  "created": 1712345678,
  "model": "gemmanet/auto",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "...response..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

**Error response:**

```json
{
  "error": {
    "message": "Invalid API key",
    "type": "authentication_error",
    "code": "invalid_api_key"
  }
}
```

#### GET /v1/models

List available models (capabilities) on the network.

**Auth required:** No

**Response:**

```json
{
  "object": "list",
  "data": [
    {"id": "gemmanet/auto", "object": "model", "owned_by": "gemmanet"},
    {"id": "gemmanet/chat", "object": "model", "owned_by": "gemmanet"},
    {"id": "gemmanet/translate", "object": "model", "owned_by": "gemmanet"},
    {"id": "gemmanet/summarize", "object": "model", "owned_by": "gemmanet"},
    {"id": "gemmanet/code", "object": "model", "owned_by": "gemmanet"}
  ]
}
```

### Available Models

| Model | Description |
|-------|-------------|
| `gemmanet/auto` | Automatically routes to best available node |
| `gemmanet/chat` | General chat |
| `gemmanet/translate` | Translation |
| `gemmanet/summarize` | Summarization |
| `gemmanet/code` | Code generation |
