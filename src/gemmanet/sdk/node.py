"""Node class - developers use this to provide AI services."""
import asyncio
import logging
import time
from uuid import uuid4

import websockets

from gemmanet.sdk.models import (
    MsgType, make_ws_msg, parse_ws_msg,
)

logger = logging.getLogger("gemmanet.node")


class Node:
    def __init__(self, name: str, capabilities: list[str],
                 languages: list[str] | None = None,
                 coordinator_url: str = 'ws://localhost:8800/ws/node',
                 model_info: dict | None = None):
        self.node_id = str(uuid4())
        self.name = name
        self.capabilities = capabilities
        self.languages = languages or []
        self.coordinator_url = coordinator_url
        self.model_info = model_info or {}
        self._handlers: dict[str, callable] = {}
        self._running = False
        self._ws = None
        self._balance = 0
        self._active_tasks = 0
        self._stats = {
            'tasks_completed': 0,
            'tasks_failed': 0,
            'credits_earned': 0,
        }

    def register_handler(self, task_type: str, handler: callable):
        self._handlers[task_type] = handler

    def start(self):
        try:
            asyncio.run(self._async_start())
        except KeyboardInterrupt:
            logger.info("Node shutting down via KeyboardInterrupt")
            self._running = False

    async def _async_start(self):
        self._running = True
        backoff = 1
        while self._running:
            try:
                async with websockets.connect(self.coordinator_url) as ws:
                    self._ws = ws
                    backoff = 1
                    logger.info("Connected to coordinator at %s", self.coordinator_url)

                    # Send registration
                    reg_msg = make_ws_msg(MsgType.NODE_REGISTER, {
                        'node_id': self.node_id,
                        'name': self.name,
                        'capabilities': self.capabilities,
                        'languages': self.languages,
                        'model_info': self.model_info,
                    })
                    await ws.send(reg_msg)
                    logger.info("Registered as %s (id=%s)", self.name, self.node_id)

                    # Start heartbeat
                    heartbeat_task = asyncio.create_task(self._send_heartbeat())

                    try:
                        async for raw in ws:
                            if not self._running:
                                break
                            try:
                                msg = parse_ws_msg(raw)
                            except Exception:
                                logger.warning("Failed to parse message: %s", raw[:100])
                                continue

                            if msg.msg_type == MsgType.TASK_ASSIGN:
                                asyncio.create_task(self._handle_task(msg.payload))
                            elif msg.msg_type == MsgType.CREDIT_UPDATE:
                                self._balance = msg.payload.get('balance', self._balance)
                                change = msg.payload.get('change', 0)
                                self._stats['credits_earned'] += max(0, change)
                                logger.info("Credit update: balance=%d (change=%+d)",
                                            self._balance, change)
                            elif msg.msg_type == MsgType.ERROR:
                                logger.error("Error from coordinator: %s",
                                             msg.payload.get('message', ''))
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass

            except (websockets.ConnectionClosed, OSError, ConnectionRefusedError) as e:
                self._ws = None
                if not self._running:
                    break
                logger.warning("Disconnected: %s. Reconnecting in %ds...", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

        self._ws = None
        logger.info("Node stopped")

    async def _handle_task(self, payload: dict):
        task_id = payload.get('task_id', '')
        task_type = payload.get('task_type', '')
        content = payload.get('content', '')
        params = payload.get('params', {})

        logger.info("Received task %s (type=%s)", task_id, task_type)
        self._active_tasks += 1

        handler = self._handlers.get(task_type)
        start_time = time.monotonic()

        if handler is None:
            result_str = f"No handler registered for task type: {task_type}"
            status = 'failed'
            logger.warning("No handler for task type: %s", task_type)
        else:
            try:
                result_str = handler(content, **params)
                status = 'completed'
                self._stats['tasks_completed'] += 1
                logger.info("Task %s completed", task_id)
            except Exception as e:
                result_str = str(e)
                status = 'failed'
                self._stats['tasks_failed'] += 1
                logger.error("Task %s failed: %s", task_id, e)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        self._active_tasks -= 1

        if self._ws:
            result_msg = make_ws_msg(MsgType.TASK_RESULT, {
                'task_id': task_id,
                'node_id': self.node_id,
                'status': status,
                'result': result_str,
                'processing_time_ms': elapsed_ms,
            })
            try:
                await self._ws.send(result_msg)
            except Exception as e:
                logger.error("Failed to send task result: %s", e)

    async def _send_heartbeat(self):
        while self._running:
            await asyncio.sleep(30)
            if self._ws:
                try:
                    cpu = 0.0
                    try:
                        import psutil
                        cpu = psutil.cpu_percent()
                    except ImportError:
                        pass
                    hb_msg = make_ws_msg(MsgType.HEARTBEAT, {
                        'node_id': self.node_id,
                        'active_tasks': self._active_tasks,
                        'cpu_percent': cpu,
                    })
                    await self._ws.send(hb_msg)
                except Exception as e:
                    logger.warning("Failed to send heartbeat: %s", e)

    def stop(self):
        self._running = False

    def status(self) -> dict:
        return {
            'node_id': self.node_id,
            'name': self.name,
            'running': self._running,
            'capabilities': self.capabilities,
            'languages': self.languages,
            'balance': self._balance,
            'stats': dict(self._stats),
        }

    @property
    def balance(self) -> int:
        return self._balance
