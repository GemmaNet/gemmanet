"""In-flight task tracking between HTTP requests and node connections."""
import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger('gemmanet.coordinator.tasks')


class NodeDisconnected(Exception):
    """The node holding a task went away before returning a result."""


@dataclass
class PendingTask:
    task_id: str
    node_id: str
    connection: object  # the node WebSocket the task was sent on
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: float = field(default_factory=lambda: asyncio.get_running_loop().time())
    finished_at: float | None = None

    @property
    def elapsed_ms(self) -> int:
        end = self.finished_at or asyncio.get_running_loop().time()
        return int((end - self.created_at) * 1000)


class TaskTracker:
    """Routes chunks/results arriving on node connections to waiting requests.

    Every event is checked against the connection the task was assigned on,
    so a node can only answer tasks it was actually given.
    """

    def __init__(self, max_age: float | None = None):
        self._tasks: dict[str, PendingTask] = {}
        self.max_age = max_age

    def create(self, task_id: str, node_id: str, connection) -> PendingTask:
        task = PendingTask(task_id=task_id, node_id=node_id, connection=connection)
        self._evict_expired(task.created_at)
        self._tasks[task_id] = task
        return task

    def _evict_expired(self, now: float):
        """Drop entries nobody will ever pop (e.g. a stream never started)."""
        if self.max_age is None:
            return
        expired = [tid for tid, t in self._tasks.items() if now - t.created_at > self.max_age]
        for tid in expired:
            logger.warning('Evicting abandoned task %s', tid)
            del self._tasks[tid]

    def __len__(self) -> int:
        return len(self._tasks)

    def pop(self, task_id: str) -> PendingTask | None:
        return self._tasks.pop(task_id, None)

    def _owned(self, task_id: str, connection) -> PendingTask | None:
        task = self._tasks.get(task_id)
        if task is None:
            logger.debug('Event for unknown or finished task %s', task_id)
            return None
        if task.connection is not connection:
            logger.warning('Ignoring event for task %s from a node it was not assigned to',
                           task_id)
            return None
        return task

    def add_chunk(self, task_id: str, connection, delta: str):
        task = self._owned(task_id, connection)
        if task is not None and task.finished_at is None:
            task.events.put_nowait(('chunk', delta))

    def resolve(self, task_id: str, connection, payload: dict):
        task = self._owned(task_id, connection)
        if task is not None and task.finished_at is None:
            task.finished_at = asyncio.get_running_loop().time()
            task.events.put_nowait(('result', payload))

    def fail_connection(self, connection):
        """Fail every unfinished task that was sent on a closed connection."""
        for task in self._tasks.values():
            if task.connection is connection and task.finished_at is None:
                task.finished_at = asyncio.get_running_loop().time()
                task.events.put_nowait(('error', NodeDisconnected(task.node_id)))


async def next_event(task: PendingTask, deadline: float) -> tuple[str, object]:
    """Wait for the task's next event until the loop-time deadline."""
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(task.events.get(), timeout=remaining)


async def wait_result(task: PendingTask, deadline: float) -> dict:
    """Wait for the final result payload, ignoring streamed chunks."""
    while True:
        kind, value = await next_event(task, deadline)
        if kind == 'result':
            return value
        if kind == 'error':
            raise value
