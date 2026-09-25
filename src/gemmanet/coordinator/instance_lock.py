"""Guard against running more than one coordinator process.

Node connections and in-flight tasks live in process memory, so a second
worker (uvicorn --workers N) or a second machine sharing the same Redis would
silently lose tasks. Until cross-process dispatch exists
(docs/design/multi-instance.md), the coordinator holds a Redis lock and
refuses to start while another live instance holds it.
"""
import asyncio
import logging
import os
import socket
import uuid

logger = logging.getLogger('gemmanet.coordinator.lock')

LOCK_KEY = 'gn:coordinator:instance'

_REFRESH = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class SingleInstanceLock:
    def __init__(self, redis, ttl: int = 15, wait: float = 20.0):
        self.redis = redis
        self.ttl = ttl
        # Wait a little longer than the TTL so a crashed predecessor's lock
        # can expire while a supervisor (systemd etc.) restarts us.
        self.wait = wait
        self.token = f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}'
        self._refresher: asyncio.Task | None = None

    async def acquire(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait
        while not await self.redis.set(LOCK_KEY, self.token, nx=True, ex=self.ttl):
            if loop.time() >= deadline:
                holder = await self.redis.get(LOCK_KEY)
                raise RuntimeError(
                    f'Another GemmaNet coordinator ({holder}) is running against this '
                    'Redis. Only one coordinator process is supported: do not use '
                    'uvicorn --workers > 1. See docs/design/multi-instance.md.')
            await asyncio.sleep(1)
        self._refresher = asyncio.create_task(self._refresh_loop())
        logger.info(f'Coordinator instance lock acquired ({self.token})')

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(self.ttl / 3)
            try:
                if await self.redis.eval(_REFRESH, 1, LOCK_KEY, self.token, self.ttl * 1000):
                    continue
                # Key vanished (e.g. Redis restarted): take it back if free.
                if not await self.redis.set(LOCK_KEY, self.token, nx=True, ex=self.ttl):
                    logger.error('Coordinator instance lock is held by another process; '
                                 'running several coordinators is not supported')
            except Exception as e:
                logger.warning(f'Instance lock refresh failed: {e}')

    async def release(self):
        if self._refresher:
            self._refresher.cancel()
            try:
                await self._refresher
            except asyncio.CancelledError:
                pass
        await self.redis.eval(_RELEASE, 1, LOCK_KEY, self.token)
