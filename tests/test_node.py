"""Node SDK task execution: handler shapes, streaming, and non-blocking I/O."""
import asyncio
import time

from gemmanet import Completion, Node
from gemmanet.sdk.models import MsgType, parse_ws_msg


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(parse_ws_msg(raw))

    def of(self, msg_type):
        return [m.payload for m in self.sent if m.msg_type == msg_type]


def make_node(handler, task_type='x'):
    node = Node(name='t', capabilities=[task_type], api_key='gn_test')
    node.register_handler(task_type, handler)
    node._ws = FakeWS()
    node.node_id = 'node-1'
    return node


def run_task(node, content='hi', params=None, stream=False, task_id='t1'):
    asyncio.run(node._handle_task({
        'task_id': task_id, 'task_type': 'x', 'content': content,
        'params': params or {}, 'stream': stream,
    }))
    return node._ws.of(MsgType.TASK_RESULT)[-1]


def test_sync_handler_does_not_block_event_loop():
    def slow(content, **params):
        time.sleep(0.3)
        return content.upper()

    node = make_node(slow)

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        t = asyncio.create_task(ticker())
        await node._handle_task({'task_id': 't1', 'task_type': 'x',
                                 'content': 'hi', 'params': {}})
        t.cancel()
        return ticks

    ticks = asyncio.run(main())
    assert ticks > 10  # heartbeats and other tasks keep running meanwhile
    result = node._ws.of(MsgType.TASK_RESULT)[-1]
    assert result['status'] == 'completed'
    assert result['result'] == 'HI'


def test_slow_sync_tasks_run_concurrently():
    def slow(content, **params):
        time.sleep(0.3)
        return content

    node = make_node(slow)

    async def main():
        start = time.monotonic()
        await asyncio.gather(*(node._handle_task({
            'task_id': f't{i}', 'task_type': 'x', 'content': 'c', 'params': {}})
            for i in range(3)))
        return time.monotonic() - start

    assert asyncio.run(main()) < 0.8
    assert len(node._ws.of(MsgType.TASK_RESULT)) == 3


def test_async_handler():
    async def handler(content, **params):
        await asyncio.sleep(0)
        return f'async {content}'

    assert run_task(make_node(handler))['result'] == 'async hi'


def test_params_are_passed_as_keyword_arguments():
    def handler(content, prefix='?', **params):
        return f'{prefix}:{content}'

    assert run_task(make_node(handler), params={'prefix': 'P'})['result'] == 'P:hi'


def test_generator_handler_streams_chunks_when_requested():
    def handler(content, **params):
        yield 'a'
        yield 'b'
        yield 'c'
        return {'prompt_tokens': 2, 'completion_tokens': 3}

    node = make_node(handler)
    result = run_task(node, stream=True)
    assert [c['delta'] for c in node._ws.of(MsgType.TASK_CHUNK)] == ['a', 'b', 'c']
    assert result['result'] == 'abc'
    assert result['usage'] == {'prompt_tokens': 2, 'completion_tokens': 3}


def test_generator_handler_without_streaming_sends_only_result():
    def handler(content, **params):
        yield 'a'
        yield 'b'

    node = make_node(handler)
    assert run_task(node)['result'] == 'ab'
    assert node._ws.of(MsgType.TASK_CHUNK) == []


def test_async_generator_handler_streams():
    async def handler(content, **params):
        for piece in ('x', 'y'):
            yield piece

    node = make_node(handler)
    assert run_task(node, stream=True)['result'] == 'xy'
    assert len(node._ws.of(MsgType.TASK_CHUNK)) == 2


def test_stream_method_is_used_only_for_streaming_requests():
    class Handler:
        def __call__(self, content, **params):
            return Completion('whole', usage={'prompt_tokens': 1, 'completion_tokens': 1})

        def stream(self, content, **params):
            yield 'pie'
            yield 'ces'

    node = make_node(Handler())
    plain = run_task(node, task_id='t1')
    assert plain['result'] == 'whole'
    assert plain['usage'] == {'prompt_tokens': 1, 'completion_tokens': 1}

    streamed = run_task(node, stream=True, task_id='t2')
    assert streamed['result'] == 'pieces'
    assert [c['delta'] for c in node._ws.of(MsgType.TASK_CHUNK)] == ['pie', 'ces']


def test_failing_handler_reports_failed_status():
    def handler(content, **params):
        raise RuntimeError('model crashed')

    result = run_task(make_node(handler))
    assert result['status'] == 'failed'
    assert 'model crashed' in result['result']


def test_missing_handler_reports_failed_status():
    node = make_node(lambda c, **p: c)
    asyncio.run(node._handle_task({'task_id': 't1', 'task_type': 'other',
                                   'content': 'hi', 'params': {}}))
    assert node._ws.of(MsgType.TASK_RESULT)[-1]['status'] == 'failed'


def test_benchmark_uses_handler_without_blocking():
    def handler(content, **params):
        return f'ok: {content}'

    node = make_node(handler)
    asyncio.run(node._handle_benchmark({'prompts': ['p1', 'p2']}))
    results = node._ws.of(MsgType.BENCHMARK_RESULT)[-1]['results']
    assert [r['response'] for r in results] == ['ok: p1', 'ok: p2']
    assert all(r['success'] for r in results)


def test_registration_ignores_messages_before_ack():
    from gemmanet.sdk.models import make_ws_msg

    class RegisterWS(FakeWS):
        def __init__(self, incoming):
            super().__init__()
            self.incoming = list(incoming)

        async def recv(self):
            return self.incoming.pop(0)

    node = Node(name='t', capabilities=['x'], api_key='gn_test')
    ws = RegisterWS([
        make_ws_msg(MsgType.BENCHMARK, {'prompts': []}),
        make_ws_msg(MsgType.NODE_REGISTERED, {'node_id': 'assigned-id'}),
    ])
    asyncio.run(node._register(ws))
    assert node.node_id == 'assigned-id'
    assert ws.sent[0].payload['api_key'] == 'gn_test'
