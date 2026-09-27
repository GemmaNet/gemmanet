"""Resource limits: connection rates, request sizes, task parameters.

Every limit can be changed with an environment variable; see
docs/design/security-hardening.md for why each value was chosen.
"""
import json
import os
import time
from collections import deque

from fastapi import HTTPException


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


WS_CONNECTS_PER_MINUTE = _env_int('GEMMANET_WS_CONNECTS_PER_MINUTE', 30)
MAX_NODES_PER_ACCOUNT = _env_int('GEMMANET_MAX_NODES_PER_ACCOUNT', 5)
WS_MAX_MESSAGE_BYTES = _env_int('GEMMANET_WS_MAX_MESSAGE_BYTES', 4 * 1024 * 1024)
MAX_RESULT_BYTES = _env_int('GEMMANET_MAX_RESULT_BYTES', 1024 * 1024)
MAX_BODY_BYTES = _env_int('GEMMANET_MAX_BODY_BYTES', 2_000_000)

MAX_PARAMS_KEYS = 32
MAX_PARAMS_KEY_CHARS = 64
MAX_PARAMS_STRING_CHARS = 8192
MAX_PARAMS_DEPTH = 4
MAX_PARAMS_JSON_BYTES = 32 * 1024


class SlidingWindowLimiter:
    """At most `limit` events per `window` seconds for each key (in memory)."""

    MAX_KEYS = 10_000  # forget idle keys once this many are tracked

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._events: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        events = self._events.get(key)
        if events is None:
            if len(self._events) >= self.MAX_KEYS:
                self._prune(now)
            events = self._events[key] = deque()
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

    def _prune(self, now: float):
        idle = [k for k, ev in self._events.items() if not ev or now - ev[-1] >= self.window]
        for key in idle:
            del self._events[key]


def check_params(params: dict) -> dict:
    """Validate task params (they become handler keyword arguments on the node)."""
    if len(params) > MAX_PARAMS_KEYS:
        raise ValueError(f'at most {MAX_PARAMS_KEYS} params')
    for key in params:
        if not key.isidentifier() or key == 'content':
            raise ValueError(f'invalid params key: {key!r}')
        if len(key) > MAX_PARAMS_KEY_CHARS:
            raise ValueError(f'params keys may have at most {MAX_PARAMS_KEY_CHARS} characters')
    _check_value(params, depth=1)
    if len(json.dumps(params, ensure_ascii=False).encode()) > MAX_PARAMS_JSON_BYTES:
        raise ValueError(f'params may be at most {MAX_PARAMS_JSON_BYTES} bytes as JSON')
    return params


def _check_value(value, depth: int):
    if isinstance(value, str):
        if len(value) > MAX_PARAMS_STRING_CHARS:
            raise ValueError(
                f'params strings may have at most {MAX_PARAMS_STRING_CHARS} characters')
    elif isinstance(value, dict | list):
        if depth > MAX_PARAMS_DEPTH:
            raise ValueError(f'params may be nested at most {MAX_PARAMS_DEPTH} levels')
        items = value.items() if isinstance(value, dict) else enumerate(value)
        for key, item in items:
            if isinstance(key, str):
                _check_value(key, depth)
            _check_value(item, depth + 1)


class BodySizeLimitMiddleware:
    """Reject HTTP request bodies over `max_bytes` with 413.

    Checks Content-Length up front and also counts the bytes actually
    received, so chunked uploads without a length are bounded too.
    """

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        for name, value in scope.get('headers', []):
            if name == b'content-length':
                try:
                    too_big = int(value) > self.max_bytes
                except ValueError:
                    too_big = False
                if too_big:
                    return await _send_413(send)

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > self.max_bytes:
                    # FastAPI answers an HTTPException raised while reading
                    # the body itself (any other error would become a 400).
                    raise HTTPException(status_code=413, detail='Request body too large')
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except HTTPException as e:
            # Reached only if the body was read outside a route handler.
            if e.status_code != 413 or response_started:
                raise
            await _send_413(send)


async def _send_413(send):
    body = json.dumps({'detail': 'Request body too large'}).encode()
    await send({'type': 'http.response.start', 'status': 413,
                'headers': [(b'content-type', b'application/json'),
                            (b'content-length', str(len(body)).encode())]})
    await send({'type': 'http.response.body', 'body': body})
