"""Coordinator task tracking: only the assigned connection can answer a task."""
import asyncio

import pytest

from gemmanet.coordinator.tasks import (
    NodeDisconnected,
    ResultTooLarge,
    TaskTracker,
    next_event,
    wait_result,
)


def test_result_from_other_connection_is_ignored():
    async def main():
        loop = asyncio.get_running_loop()
        tracker = TaskTracker()
        assigned, other = object(), object()
        task = tracker.create('t1', 'node-a', assigned)

        tracker.resolve('t1', other, {'status': 'completed', 'result': 'forged'})
        with pytest.raises(TimeoutError):
            await wait_result(task, loop.time() + 0.1)

        tracker.resolve('t1', assigned, {'status': 'completed', 'result': 'real'})
        return await wait_result(task, loop.time() + 1)

    assert asyncio.run(main())['result'] == 'real'


def test_closed_connection_fails_its_tasks_immediately():
    async def main():
        loop = asyncio.get_running_loop()
        tracker = TaskTracker()
        conn = object()
        task = tracker.create('t1', 'node-a', conn)
        untouched = tracker.create('t2', 'node-b', object())
        tracker.fail_connection(conn)
        with pytest.raises(NodeDisconnected):
            await wait_result(task, loop.time() + 1)
        assert untouched.events.empty()

    asyncio.run(main())


def test_chunks_arrive_before_result():
    async def main():
        loop = asyncio.get_running_loop()
        tracker = TaskTracker()
        conn = object()
        task = tracker.create('t1', 'node-a', conn)
        tracker.add_chunk('t1', conn, 'he')
        tracker.add_chunk('t1', object(), 'forged')
        tracker.add_chunk('t1', conn, 'llo')
        tracker.resolve('t1', conn, {'status': 'completed', 'result': 'hello'})
        tracker.add_chunk('t1', conn, 'late')  # after the result: dropped
        deadline = loop.time() + 1
        return [await next_event(task, deadline) for _ in range(3)], task.events.empty()

    events, drained = asyncio.run(main())
    assert events[0] == ('chunk', 'he')
    assert events[1] == ('chunk', 'llo')
    assert events[2][0] == 'result'
    assert drained


def test_unknown_task_events_are_ignored():
    async def main():
        tracker = TaskTracker()
        tracker.resolve('nope', object(), {})
        tracker.add_chunk('nope', object(), 'x')

    asyncio.run(main())


def test_abandoned_tasks_are_evicted_after_max_age():
    async def main():
        tracker = TaskTracker(max_age=0.05)
        tracker.create('old', 'node-a', object())
        await asyncio.sleep(0.1)
        tracker.create('new', 'node-a', object())
        return len(tracker), tracker.pop('old'), tracker.pop('new')

    size, old, new = asyncio.run(main())
    assert size == 1
    assert old is None
    assert new is not None


def test_streamed_output_over_the_limit_fails_the_task():
    async def main():
        loop = asyncio.get_running_loop()
        tracker = TaskTracker(max_result_bytes=10)
        conn = object()
        task = tracker.create('t1', 'node-a', conn)
        tracker.add_chunk('t1', conn, 'héllo')     # 6 bytes
        tracker.add_chunk('t1', conn, 'abcd')      # 10: at the limit
        tracker.add_chunk('t1', conn, 'x')         # 11: over
        tracker.add_chunk('t1', conn, 'ignored')   # the task is already finished
        tracker.resolve('t1', conn, {'status': 'completed', 'result': 'late'})
        events = [await next_event(task, loop.time() + 1) for _ in range(3)]
        assert task.events.empty()
        return events

    events = asyncio.run(main())
    assert events[:2] == [('chunk', 'héllo'), ('chunk', 'abcd')]
    kind, error = events[2]
    assert kind == 'error' and isinstance(error, ResultTooLarge)


def test_final_result_over_the_limit_fails_the_task():
    async def main():
        loop = asyncio.get_running_loop()
        tracker = TaskTracker(max_result_bytes=10)
        conn = object()
        ok = tracker.create('ok', 'node-a', conn)
        tracker.resolve('ok', conn, {'status': 'completed', 'result': 'x' * 10})
        assert (await wait_result(ok, loop.time() + 1))['result'] == 'x' * 10
        big = tracker.create('big', 'node-a', conn)
        tracker.resolve('big', conn, {'status': 'completed', 'result': '字' * 4})  # 12 bytes
        with pytest.raises(ResultTooLarge):
            await wait_result(big, loop.time() + 1)

    asyncio.run(main())
