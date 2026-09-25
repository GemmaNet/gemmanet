# GemmaNet API Reference

Base URL: `http://localhost:8800`

Authenticated endpoints take the API key as `Authorization: Bearer <api_key>`.
Get a key from `POST /api/v1/register`.

## POST /api/v1/register

Create an account and API key.

**Auth required:** No (rate limited to 5 per hour per IP)

**Request body (optional):**

```json
{"email": "you@example.com"}
```

**Response:**

```json
{
  "api_key": "gn_4f1c2a9e0b7d5e339c1a7d2e8f6a1b00",
  "account_id": "3bd40195-244e-43b2-ae32-f83e8f90e776"
}
```

The key is shown only once; the coordinator stores just its hash.

---

## GET /api/v1/status

Get the current network status.

**Auth required:** No

**Response:**

```json
{
  "status": "running",
  "version": "0.2.0a1",
  "online_nodes": 3,
  "total_tasks_today": 42
}
```

`total_tasks_today` counts successfully completed tasks since 00:00 UTC.

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
    "node_id": "7c4f6a2e-3b1d-5f8e-9a0c-1d2e3f4a5b6c",
    "name": "zh-specialist",
    "capabilities": ["translate"],
    "languages": ["en", "zh"],
    "model_info": {},
    "cpu_percent": 12.5,
    "active_tasks": 0
  }
]
```

---

## POST /api/v1/request

Submit a task. The coordinator routes it to the best available node.

**Auth required:** Yes

**Request body:**

```json
{
  "task_type": "echo",
  "content": "Hello world!",
  "params": {"prefix": "Echo"},
  "stream": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `task_type` | string | Capability to use (e.g., `echo`, `translate`) |
| `content` | string | Content to process (up to 200,000 characters) |
| `params` | object | Keyword arguments for the node's handler; keys must be identifiers other than `content` |
| `stream` | bool | Stream the result as Server-Sent Events (see below) |

**Response (200):**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "result": "Echo: Hello world!",
  "node_id": "7c4f6a2e-3b1d-5f8e-9a0c-1d2e3f4a5b6c",
  "processing_time_ms": 15,
  "usage": null
}
```

`status` is `failed` when the node's handler raised an error; `result` then
holds the error message. Failures count against the node's reputation.

`translate` tasks longer than 1,000 characters are split across several nodes
and merged (`GEMMANET_SPLIT_TASKS` configures which task types may be split).

**Streaming (`"stream": true`):** the response is `text/event-stream` with
`data:` lines of JSON:

```text
data: {"delta": "Once "}
data: {"delta": "upon a time"}
data: {"done": true, "result": { ...TaskResult... }}
```

or, if the task fails, `data: {"error": {"code": "task_failed", "message": "..."}, "task_id": "..."}`.

A stream may run longer than the task timeout as long as the node keeps
sending chunks: the timeout then limits the silence between chunks, up to
`GEMMANET_STREAM_MAX_SECONDS` (600 s) in total.

**Errors:**

| Code | Detail |
|------|--------|
| 401 | Invalid or missing API key |
| 404 | No node available for this task type |
| 422 | Invalid request body or params |
| 502 | Node disconnected before returning a result |
| 504 | Task timed out (60s, `GEMMANET_TASK_TIMEOUT`) |

---

## POST /api/v1/rate

Rate a task you requested (1-5 stars). Ratings feed the reputation of the
node(s) that served it. Each task can be rated once, within an hour.

**Auth required:** Yes (must be the account that requested the task)

**Request body:**

```json
{"task_id": "550e8400-e29b-41d4-a716-446655440000", "rating": 5}
```

**Response:**

```json
{"status": "rated", "node_ids": ["7c4f6a2e-..."], "rating": 5}
```

**Errors:** 400 rating out of range, 403 not your task, 404 task unknown or
expired, 409 already rated.

---

## GET /api/v1/reputation/{node_id}

Reputation of one node.

**Auth required:** No

```json
{
  "node_id": "7c4f6a2e-...",
  "score": 83.5,
  "total_tasks": 120,
  "success_rate": 0.975,
  "avg_response_ms": 850,
  "avg_rating": 4.6,
  "total_ratings": 14
}
```

The score (0-100) is 40% completion rate, 20% speed, 20% recent activity and
20% user ratings; new nodes start at 50. Nodes scoring below 35 after more than
10 tasks are suspended from routing for 24 hours.

## GET /api/v1/leaderboard

Top nodes by reputation. Query `limit` (1-100, default 20). Entries are the
reputation objects above plus `name` (for nodes currently online).

## GET /api/v1/benchmark/{node_id}

The node's latest benchmark profile (`avg_response_ms`,
`estimated_tokens_per_sec`, `benchmark_passed`, `results`, `timestamp`), or
`{"benchmark": null}`. Timing is measured by the coordinator.

---

## POST /api/v1/feedback

Send feedback. **Auth:** optional. Body: `{"type": "bug" | "feature" | "other", "message": "...", "email": null}`.

## GET /api/v1/feedback

List feedback. **Auth:** the coordinator's `ADMIN_KEY` as Bearer token; if no
`ADMIN_KEY` is configured the endpoint always returns 401.

---

## WebSocket /ws/node

Node connection endpoint. Nodes connect here to register, receive tasks, and
send results. The SDK's `Node` class implements this protocol.

**Connection flow:**

1. Node connects to `ws://localhost:8800/ws/node`
2. Node sends `node_register` with its API key, name, capabilities, languages and model info
3. Coordinator answers `node_registered` with the node's id, or `error`
   (`auth_failed`, close code 4001; `invalid_registration`, close code 1008)
4. Coordinator sends a `benchmark`; the node replies with `benchmark_result`
5. Node receives `task_assign` messages and answers each with `task_result`
   (preceded by `task_chunk` messages when the task asked for streaming)
6. Node sends `heartbeat` messages every 30s

The node id is derived from the API key's account and the node name, so it is
stable across restarts. If a second connection registers the same identity,
the older one is closed with code 4000 and should not reconnect.

**Message format:**

```json
{
  "msg_id": "unique-id",
  "msg_type": "task_assign",
  "payload": { ... },
  "sender_id": "",
  "timestamp": "2026-09-25T10:30:00Z"
}
```

**Message types:**

| Type | Direction | Payload |
|------|-----------|---------|
| `node_register` | Node -> Coordinator | `api_key`, `name`, `capabilities`, `languages`, `model_info` |
| `node_registered` | Coordinator -> Node | `node_id` |
| `heartbeat` | Node -> Coordinator | `active_tasks`, `cpu_percent` |
| `task_assign` | Coordinator -> Node | `task_id`, `task_type`, `content`, `params`, `stream` |
| `task_chunk` | Node -> Coordinator | `task_id`, `delta` |
| `task_result` | Node -> Coordinator | `task_id`, `status`, `result`, `processing_time_ms`, `usage` |
| `benchmark` | Coordinator -> Node | `prompts` |
| `benchmark_result` | Node -> Coordinator | `results` |
| `error` | Coordinator -> Node | `code`, `message` |

A node can only answer tasks that were assigned to its own connection.

---

## OpenAI Compatible API

Any application using the OpenAI SDK can switch to GemmaNet by changing two lines:

```python
from openai import OpenAI
client = OpenAI(
    base_url='https://api.gemmanet.net/v1',  # Change this
    api_key='gn_your_key',                     # Change this
)
# Everything else stays the same!
```

### POST /v1/chat/completions

**Auth required:** Yes

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

The whole conversation reaches the node as `params['messages']` (chat-aware
handlers such as `OllamaHandler` use it); `content` holds the last user
message, prefixed by the last system message. `max_tokens` and `temperature`
are passed as params. With `"stream": true` the response streams
`chat.completion.chunk` events as the node generates text.

**Response (200):**

```json
{
  "id": "chatcmpl-550e8400-...",
  "object": "chat.completion",
  "created": 1712345678,
  "model": "gemmanet/auto",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "...response..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 21,
    "completion_tokens": 9,
    "total_tokens": 30
  }
}
```

`usage` is reported by the node (e.g. `OllamaHandler`); nodes that don't
report it yield zeros.

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

Error codes: `invalid_api_key` (401), `context_length_exceeded` (400),
`node_error` (502, handler failed), `node_disconnected` (502),
`no_node_available` (503), `timeout` (504).

### GET /v1/models

List available models (capabilities) on the network.

**Auth required:** No

```json
{
  "object": "list",
  "data": [
    {"id": "gemmanet/auto", "object": "model", "owned_by": "gemmanet"},
    {"id": "gemmanet/chat", "object": "model", "owned_by": "gemmanet"},
    {"id": "gemmanet/translate", "object": "model", "owned_by": "gemmanet"}
  ]
}
```

### Models

| Model | Description |
|-------|-------------|
| `gemmanet/auto` | Chat, routed to the best available node |
| `gemmanet/<capability>` | Any capability offered by online nodes, e.g. `gemmanet/translate`, `gemmanet/summarize`, `gemmanet/code` |
| anything else | Treated as `gemmanet/auto` |
