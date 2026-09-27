"""Resource limits (M4): rate limiter, task params, request body size."""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gemmanet.coordinator import limits
from gemmanet.coordinator.limits import BodySizeLimitMiddleware, SlidingWindowLimiter, check_params
from gemmanet.coordinator.server import ChatCompletionRequest, RequestBody
from gemmanet.coordinator.ws_manager import WSConnectionManager


def test_sliding_window_limits_each_key(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(limits.time, 'monotonic', lambda: now[0])
    limiter = SlidingWindowLimiter(3, window=60)
    assert [limiter.allow('1.2.3.4') for _ in range(4)] == [True, True, True, False]
    assert limiter.allow('5.6.7.8')          # other clients are unaffected
    now[0] += 60
    assert limiter.allow('1.2.3.4')          # the window has moved on


def test_sliding_window_forgets_idle_keys(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(limits.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(SlidingWindowLimiter, 'MAX_KEYS', 10)
    limiter = SlidingWindowLimiter(1, window=60)
    for i in range(10):
        limiter.allow(f'ip{i}')
    now[0] += 61
    limiter.allow('new')
    assert len(limiter._events) == 1


@pytest.mark.parametrize('params', [
    {f'k{i}': 1 for i in range(33)},                       # too many keys
    {'k' * 65: 1},                                         # key too long
    {'text': 'x' * 8193},                                  # string too long
    {'items': ['x' * 8193]},                               # ... also when nested
    {'a': {'b': {'c': {'d': {'e': 1}}}}},                  # nested too deep
    {f'k{i}': 'x' * 4000 for i in range(9)},               # over 32 KiB as JSON
    {'content': 'x'}, {'not-an-identifier': 1}, {'1x': 1},  # not handler kwargs
])
def test_params_over_the_limits_are_rejected(params):
    with pytest.raises(ValidationError):
        RequestBody(task_type='echo', content='x', params=params)


def test_params_at_the_limits_are_accepted():
    check_params({f'k{i}': 1 for i in range(32)})
    check_params({'k' * 64: 'x' * 8192})
    check_params({'a': {'b': {'c': {'d': 1}}}})
    check_params({'source_lang': 'en', 'target_lang': 'zh', 'messages': [
        {'role': 'user', 'content': 'hi'}]})


def test_openai_model_and_role_are_bounded():
    with pytest.raises(ValidationError):
        ChatCompletionRequest(model='m' * 129, messages=[{'role': 'user', 'content': 'x'}])
    with pytest.raises(ValidationError):
        ChatCompletionRequest(messages=[{'role': 'r' * 33, 'content': 'x'}])
    ChatCompletionRequest(model='m' * 128, messages=[{'role': 'r' * 32, 'content': 'x'}])


@pytest.fixture
def echo_app():
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=1000)

    @app.post('/echo')
    async def echo(request: Request):
        return {'size': len(await request.body())}

    return TestClient(app)


def test_body_at_the_limit_is_accepted(echo_app):
    assert echo_app.post('/echo', content=b'x' * 1000).json() == {'size': 1000}


def test_body_over_the_limit_is_refused_by_content_length(echo_app):
    resp = echo_app.post('/echo', content=b'x' * 1001)
    assert resp.status_code == 413


def test_chunked_body_over_the_limit_is_refused(echo_app):
    def chunks():  # no Content-Length: the bytes are counted as they arrive
        for _ in range(11):
            yield b'x' * 100
    resp = echo_app.post('/echo', content=chunks())
    assert resp.status_code == 413


def test_node_limit_counts_accounts_and_ignores_replacements():
    manager = WSConnectionManager()
    for name in ('n1', 'n2'):
        manager.attach(name, object(), {'name': name}, 'acct')
    manager.attach('other', object(), {'name': 'other'}, 'acct-2')
    assert manager.exceeds_node_limit('acct', 'n3', limit=2)        # a third node
    assert not manager.exceeds_node_limit('acct', 'n1', limit=2)    # n1 reconnecting
    assert not manager.exceeds_node_limit('acct-2', 'x', limit=2)
    assert 'acct' not in str(manager.get_online_nodes())            # account stays private
    ws = manager.get('n2')
    manager.detach('n2', ws)
    assert not manager.exceeds_node_limit('acct', 'n3', limit=2)


class _FakeNodeSocket:
    """Just enough of a WebSocket to drive the coordinator's node handler."""

    def __init__(self, name, gate, arrived):
        from gemmanet.sdk.models import MsgType, make_ws_msg
        self.client = type('Client', (), {'host': '203.0.113.7'})()
        self.register = make_ws_msg(MsgType.NODE_REGISTER, {
            'api_key': 'gn_test', 'name': name, 'capabilities': ['echo']})
        self.sent, self.close_code = [], None
        self.gate, self.arrived = gate, arrived
        self.closed = None
        self._first = True

    async def accept(self):
        import asyncio
        self.closed = asyncio.Event()

    async def receive_text(self):
        from fastapi import WebSocketDisconnect
        if self._first:
            self._first = False
            return self.register
        await self.closed.wait()
        raise WebSocketDisconnect(1000)

    async def send_text(self, text):
        self.sent.append(text)
        if 'node_registered' in text:
            # Hold every registration between the first limit check and
            # attaching, the window where concurrent registrations race.
            self.arrived.append(self)
            await self.gate.wait()

    async def close(self, code=1000):
        self.close_code = code
        if self.closed is not None:
            self.closed.set()


def test_racing_registrations_cannot_overshoot_the_node_cap(monkeypatch):
    import asyncio

    from gemmanet.coordinator import server
    from gemmanet.coordinator.tasks import TaskTracker

    class FakeRegistry:
        async def register(self, node_id, info): pass
        async def unregister(self, node_id): pass

    async def main():
        manager = WSConnectionManager()
        monkeypatch.setattr(server.app.state, 'ws_manager', manager, raising=False)
        monkeypatch.setattr(server.app.state, 'registry', FakeRegistry(), raising=False)
        monkeypatch.setattr(server.app.state, 'tracker', TaskTracker(), raising=False)
        monkeypatch.setattr(server, 'MAX_NODES_PER_ACCOUNT', 2)
        monkeypatch.setattr(server, 'ws_connect_limiter', SlidingWindowLimiter(100))

        async def account_for_key(key):
            return 'acct'
        monkeypatch.setattr(server, '_account_for_key', account_for_key)

        gate, arrived = asyncio.Event(), []
        sockets = [_FakeNodeSocket(f'n{i}', gate, arrived) for i in range(4)]
        handlers = [asyncio.create_task(server.node_websocket(ws)) for ws in sockets]
        while len(arrived) < 4:            # all four passed the first check
            await asyncio.sleep(0.01)
        gate.set()
        await asyncio.sleep(0.1)
        online = manager.account_nodes('acct')
        refused = [ws for ws in sockets if ws.close_code == server.CLOSE_POLICY_VIOLATION]
        for ws in sockets:
            await ws.close()
        await asyncio.gather(*handlers)
        return online, refused

    online, refused = asyncio.run(main())
    assert len(online) == 2
    assert len(refused) == 2
    assert all('too_many_nodes' in ws.sent[-1] for ws in refused)
