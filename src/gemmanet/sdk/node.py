"""Node class - developers use this to provide AI services."""
import asyncio
import inspect
import logging
import os
import threading
import time

import websockets

from gemmanet.sdk.exceptions import AuthenticationError, GemmaNetError
from gemmanet.sdk.models import MsgType, make_ws_msg, parse_ws_msg

try:
    import psutil
except ImportError:  # optional: pip install "gemmanet[node]"
    psutil = None

logger = logging.getLogger("gemmanet.node")

# Close code the coordinator uses when another connection took over this
# node identity (same API key + node name). Reconnecting would just kick the
# other process off again, so the node stops instead.
CLOSE_REPLACED = 4000
REGISTER_TIMEOUT = 30


def _is_async_callable(fn) -> bool:
    # Also covers handler objects whose __call__ is async.
    targets = (fn, inspect.getattr_static(type(fn), '__call__', None))
    return any(check(t) for t in targets
               for check in (inspect.iscoroutinefunction, inspect.isasyncgenfunction))


def _advance(gen):
    """Step a sync generator; returns (value, done, return_value)."""
    try:
        return next(gen), False, None
    except StopIteration as stop:
        return None, True, stop.value


class Node:
    def __init__(self, name: str, capabilities: list[str],
                 languages: list[str] | None = None,
                 coordinator_url: str = 'ws://localhost:8800/ws/node',
                 model_info: dict | None = None,
                 api_key: str | None = None,
                 heartbeat_interval: float = 30.0):
        self.api_key = api_key or os.getenv('GEMMANET_API_KEY')
        if not self.api_key:
            raise ValueError('api_key is required: pass api_key=... or set '
                             'GEMMANET_API_KEY (get one from POST /api/v1/register)')
        # Assigned by the coordinator on registration; stable for the same
        # API key + node name, so reputation survives restarts.
        self.node_id: str | None = None
        self.name = name
        self.capabilities = capabilities
        self.languages = languages or []
        self.coordinator_url = coordinator_url
        self.model_info = model_info or {}
        self.heartbeat_interval = heartbeat_interval
        self._handlers: dict[str, callable] = {}
        self._running = False
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._registered = threading.Event()
        self._background: set[asyncio.Task] = set()
        self._active_tasks = 0
        self._stats = {
            'tasks_completed': 0,
            'tasks_failed': 0,
        }

    def register_handler(self, task_type: str, handler: callable):
        """Register a handler for a task type.

        A handler is called as ``handler(content, **params)`` and may be:
        a plain function returning ``str`` (run in a worker thread so it
        never blocks the connection), an ``async def`` coroutine, or a
        (sync or async) generator yielding text pieces, which are streamed to
        the caller when streaming was requested. A handler object may also
        expose a ``stream(content, **params)`` generator used for streaming.
        """
        self._handlers[task_type] = handler

    def start(self):
        try:
            asyncio.run(self._async_start())
        except KeyboardInterrupt:
            logger.info("Node shutting down via KeyboardInterrupt")
            self._running = False

    async def _async_start(self):
        self._running = True
        self._loop = asyncio.get_running_loop()
        backoff = 1
        while self._running:
            try:
                async with websockets.connect(self.coordinator_url) as ws:
                    await self._register(ws)
                    self._ws = ws
                    backoff = 1
                    heartbeat_task = asyncio.create_task(self._send_heartbeat())
                    try:
                        await self._receive_loop(ws)
                    finally:
                        self._ws = None
                        self._registered.clear()
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass

            except (TimeoutError, websockets.ConnectionClosed, OSError) as e:
                self._ws = None
                rcvd = getattr(e, 'rcvd', None)
                if rcvd is not None and rcvd.code == CLOSE_REPLACED:
                    logger.error("Another node with the same name connected "
                                 "using this API key; stopping this one")
                    self._running = False
                if not self._running:
                    break
                logger.warning("Disconnected: %s. Reconnecting in %ds...", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

        self._ws = None
        logger.info("Node stopped")

    async def _register(self, ws):
        await ws.send(make_ws_msg(MsgType.NODE_REGISTER, {
            'api_key': self.api_key,
            'name': self.name,
            'capabilities': self.capabilities,
            'languages': self.languages,
            'model_info': self.model_info,
        }))
        deadline = asyncio.get_running_loop().time() + REGISTER_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            reply = parse_ws_msg(await asyncio.wait_for(ws.recv(), max(remaining, 0)))
            if reply.msg_type == MsgType.NODE_REGISTERED:
                break
            if reply.msg_type == MsgType.ERROR:
                self._running = False
                message = reply.payload.get('message', 'registration rejected')
                if reply.payload.get('code') == 'auth_failed':
                    raise AuthenticationError(message)
                raise GemmaNetError(message)
            logger.warning("Ignoring %s received before registration completed",
                           reply.msg_type.value)
        self.node_id = reply.payload['node_id']
        self._registered.set()
        logger.info("Registered as %s (id=%s) at %s",
                    self.name, self.node_id, self.coordinator_url)

    async def _receive_loop(self, ws):
        async for raw in ws:
            if not self._running:
                break
            try:
                msg = parse_ws_msg(raw)
            except Exception:
                logger.warning("Failed to parse message: %s", raw[:100])
                continue

            if msg.msg_type == MsgType.TASK_ASSIGN:
                self._spawn(self._handle_task(msg.payload))
            elif msg.msg_type == MsgType.BENCHMARK:
                self._spawn(self._handle_benchmark(msg.payload))
            elif msg.msg_type == MsgType.ERROR:
                logger.error("Error from coordinator: %s",
                             msg.payload.get('message', ''))

    def _spawn(self, coro):
        # The event loop only keeps weak references to tasks; hold them
        # until they finish so a running handler can't be garbage-collected.
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run_handler(self, handler, content: str, params: dict,
                           on_chunk=None) -> tuple[str, dict | None]:
        """Run a handler of any supported shape; returns (text, usage)."""
        fn = handler
        stream_fn = getattr(handler, 'stream', None)
        if on_chunk is not None and callable(stream_fn):
            fn = stream_fn

        if _is_async_callable(fn):
            out = fn(content, **params)
            if inspect.isawaitable(out):
                out = await out
        else:
            out = await asyncio.to_thread(fn, content, **params)

        if not (inspect.isgenerator(out) or inspect.isasyncgen(out)):
            return str(out), getattr(out, 'usage', None)

        pieces: list[str] = []

        async def emit(piece):
            piece = str(piece)
            pieces.append(piece)
            if on_chunk is not None and piece:
                await on_chunk(piece)

        usage = None
        if inspect.isasyncgen(out):
            async for piece in out:
                await emit(piece)
        else:
            while True:
                value, done, returned = await asyncio.to_thread(_advance, out)
                if done:
                    usage = returned if isinstance(returned, dict) else None
                    break
                await emit(value)
        return ''.join(pieces), usage

    async def _send(self, msg_type: MsgType, payload: dict):
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(make_ws_msg(msg_type, payload))
        except Exception as e:
            logger.error("Failed to send %s: %s", msg_type.value, e)

    async def _handle_task(self, payload: dict):
        task_id = payload.get('task_id', '')
        task_type = payload.get('task_type', '')
        content = payload.get('content', '')
        params = payload.get('params', {}) or {}
        stream = bool(payload.get('stream', False))

        logger.info("Received task %s (type=%s)", task_id, task_type)
        self._active_tasks += 1
        handler = self._handlers.get(task_type)
        start_time = time.monotonic()
        usage = None

        async def on_chunk(piece: str):
            await self._send(MsgType.TASK_CHUNK, {'task_id': task_id, 'delta': piece})

        try:
            if handler is None:
                result_str = f"No handler registered for task type: {task_type}"
                status = 'failed'
                logger.warning("No handler for task type: %s", task_type)
            else:
                result_str, usage = await self._run_handler(
                    handler, content, params, on_chunk if stream else None)
                status = 'completed'
                self._stats['tasks_completed'] += 1
                logger.info("Task %s completed", task_id)
        except Exception as e:
            result_str = str(e)
            status = 'failed'
            self._stats['tasks_failed'] += 1
            logger.error("Task %s failed: %s", task_id, e)
        finally:
            self._active_tasks -= 1

        await self._send(MsgType.TASK_RESULT, {
            'task_id': task_id,
            'node_id': self.node_id,
            'status': status,
            'result': result_str,
            'processing_time_ms': int((time.monotonic() - start_time) * 1000),
            'usage': usage,
        })

    async def _handle_benchmark(self, payload: dict):
        prompts = payload.get('prompts', [])
        logger.info("Running benchmark with %d prompts", len(prompts))
        results = []

        handler = self._handlers.get('chat') or next(iter(self._handlers.values()), None)

        for prompt in prompts:
            start = time.monotonic()
            try:
                if handler:
                    response, _ = await self._run_handler(handler, prompt, {})
                else:
                    response = prompt  # echo if no handler
                results.append({
                    'prompt': prompt,
                    'response': response,
                    'time_ms': int((time.monotonic() - start) * 1000),
                    'success': True,
                })
            except Exception as e:
                results.append({
                    'prompt': prompt,
                    'response': str(e),
                    'time_ms': int((time.monotonic() - start) * 1000),
                    'success': False,
                })
                logger.error("Benchmark prompt failed: %s", e)

        await self._send(MsgType.BENCHMARK_RESULT, {
            'node_id': self.node_id,
            'results': results,
        })
        logger.info("Benchmark results sent")

    async def _send_heartbeat(self):
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            cpu = psutil.cpu_percent() if psutil else 0.0
            await self._send(MsgType.HEARTBEAT, {
                'node_id': self.node_id,
                'active_tasks': self._active_tasks,
                'cpu_percent': cpu,
            })

    def stop(self):
        """Stop the node; safe to call from any thread."""
        self._running = False
        loop, ws = self._loop, self._ws
        if loop is not None and ws is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(ws.close(), loop)

    def status(self) -> dict:
        return {
            'node_id': self.node_id,
            'name': self.name,
            'running': self._running,
            'capabilities': self.capabilities,
            'languages': self.languages,
            'stats': dict(self._stats),
        }
