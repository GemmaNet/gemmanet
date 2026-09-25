# Design: Multi-Instance Coordinator

**Status:** Accepted, scheduled for the next iteration · **Current behavior:** single instance, enforced by a Redis lock

## 1. Problem

The coordinator keeps two things in process memory:

- the live WebSocket of every connected node (`WSConnectionManager`), and
- every in-flight task waiting for a result (`TaskTracker`).

A request can only be served by a node connected to the *same* process. With
`uvicorn --workers N` or several machines behind a load balancer, a request
landing on worker A cannot reach a node connected to worker B; results
arriving on B cannot wake the request waiting on A. Today the coordinator
refuses to start a second instance (`instance_lock.py`) so this fails loudly
instead of silently losing tasks.

## 2. Goals and Non-Goals

**Goals**

- Any number of coordinator processes/machines share one Redis and PostgreSQL.
- A request on any instance can use any node on any instance, including streaming.
- Node identity stays unique across instances (a second connection of the same identity replaces the first, wherever it is).
- Losing an instance fails its in-flight tasks quickly (not after the full task timeout).
- Single-instance deployments keep working with no extra configuration.

**Non-goals**

- Exactly-once delivery. At-most-once plus timeouts is enough: a lost task shows up as a failed request, like today.
- Moving node connections between instances. Nodes simply reconnect wherever the load balancer sends them.

## 3. Design

### 3.1 Instance registry

Each process gets an `instance_id` at startup and keeps `gn:inst:alive:{instance_id}` alive (TTL 15 s, refreshed every 5 s). This replaces the single-instance lock.

### 3.2 Node ownership

On registration the owning instance writes `gn:node:owner:{node_id} = instance_id` (same TTL as the node entry). If another instance already owns the node, it publishes a `replace` message to that instance, which closes the old socket with code 4000 (the same semantics as today, now cross-instance).

`ws_manager.is_online()` in the router becomes "has a registry entry whose owner instance is alive".

### 3.3 Cross-instance dispatch (Redis pub/sub)

Each instance subscribes to `gn:inst:{instance_id}`. Envelopes:

| Envelope | From → To | Content |
|----------|-----------|---------|
| `assign` | requester → owner | task payload + `reply_to` instance |
| `chunk` | owner → requester | `task_id`, `delta` |
| `result` | owner → requester | `task_id`, result payload |
| `node_gone` | owner → requester | `task_id` (node disconnected) |
| `replace` | new owner → old owner | `node_id` |

- The **requesting** instance keeps the `TaskTracker` entry (futures/queues stay local, as today).
- The **owning** instance keeps a small forwarding table `task_id → (connection, reply_to)`. It still enforces "only the assigned connection may answer" before forwarding anything.
- Local nodes skip Redis entirely (fast path identical to today).

### 3.4 Failure handling

- Owner instance dies → its `alive` key expires → requesters periodically check the owners of their pending tasks and fail them with `NodeDisconnected` (502) instead of waiting for the timeout.
- Requester instance dies → the owner's `result` publish reaches nobody; the forwarding entry expires with the task timeout.

### 3.5 Other per-process state

- **Rate limits** (slowapi): switch to Redis storage (`storage_uri=REDIS_URL`) so limits apply across instances.
- **Forum rate limits** (in-memory dicts): move to Redis counters.
- **Online count** in `/api/v1/status`: count registry entries instead of local sockets.

### 3.6 Alternatives considered

- **Sticky load balancing only**: pins a node to an instance but does nothing for requests arriving elsewhere. Rejected.
- **Redis Streams instead of pub/sub**: durable and replayable, but tasks are short-lived and already time out; pub/sub is simpler and lower latency. Revisit if we need queued/offline tasks.
- **A separate message broker (NATS, RabbitMQ)**: one more service to operate; Redis is already required.

## 4. Feedback Loops

Every step is driven by a check that must pass before moving on; a failure sends us back to the step that caused it.

| Loop | Runs | Signal | On failure |
|------|------|--------|------------|
| ① Unit | every change | envelope encode/decode, forwarding-table ownership checks, liveness expiry (real Redis in CI) | fix the change |
| ② Two-instance E2E | each milestone | two coordinator processes; nodes on A, requests on B: request/response, streaming, rating, duplicate node across instances, kill A mid-task → B returns 502 in < 3 s | back to the milestone |
| ③ Load | before rollout | the load profile in §4.1 passes all its criteria | profile, adjust (e.g. batching, fast path) |
| ④ Staged rollout | after merge | staging with 2 instances for **one week**, compared with a single-instance baseline: task timeout rate, 502 rate, node flapping (4000 closes) | flip `GEMMANET_MULTI_INSTANCE=0` (restores the lock) and investigate |

### 4.1 Load profile (loop ③)

Sized from the API's own limits rather than current traffic: `/api/v1/request`
allows 60 requests/minute per client, so ~100 clients at full speed produce
~100 requests/s. The profile covers that ceiling with an order of magnitude
more nodes than an alpha network has.

| Dimension | Value |
|-----------|-------|
| Coordinator instances | 1 (baseline), 2 and 3 |
| Simulated nodes | 200, spread evenly over instances; handlers sleep 50–500 ms (random) to mimic model latency |
| Request load | ramp to 200 concurrent requests, then a steady 100 requests/s for 10 minutes (test clients use separate API keys so rate limits don't cap the run) |
| Streaming | 50 concurrent streams of ~5 s each running alongside |
| Fault injection | kill one instance mid-run |

Pass criteria (all must hold):

- **Zero lost tasks**: every request gets a result or an explicit error.
- The cross-instance hop adds **≤ 20 ms at p95** and **≤ 50 ms at p99** compared with the single-instance baseline.
- Coordinator CPU stays **< 70%** per instance.
- Memory stays flat: **< 10%** growth over the 10-minute steady phase (catches tracker/forwarding-table leaks).
- After an instance is killed, its in-flight tasks fail with 502 **within 3 s**, and tasks on other instances are unaffected.

The load generator lives in `scripts/loadtest.py` (asyncio + the SDK) so the
run can be repeated before every release.

## 5. Milestones

1. Instance registry + liveness; keep the lock unless `GEMMANET_MULTI_INSTANCE=1`.
2. Node ownership and cross-instance `replace`.
3. `assign` / `result` / `node_gone` envelopes (non-streaming) + loop ② tests.
4. `chunk` envelopes (streaming) + split tasks across instances.
5. Redis-backed rate limits; status counts from the registry.
6. Loops ③ and ④, then make multi-instance the default.

## 6. Decisions

- Load test scale: the profile in §4.1.
- A one-week staging period (loop ④) before multi-instance becomes the default.
