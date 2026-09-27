"""End-to-end: a real coordinator process with SDK nodes and clients.

Needs PostgreSQL (DATABASE_URL) and Redis; uses its own Redis database
(E2E_REDIS_URL, default db 2), which it flushes.
"""
import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

from gemmanet import Client, Completion, Node
from gemmanet.sdk.exceptions import AuthenticationError, GemmaNetError

E2E_REDIS_URL = os.getenv('E2E_REDIS_URL', 'redis://localhost:6379/2')
# Coordinators with lowered limits run next to the main one; each needs its
# own Redis database (one coordinator per database).
LIMITS_REDIS_URL = E2E_REDIS_URL.rsplit('/', 1)[0] + '/3'
RATE_REDIS_URL = E2E_REDIS_URL.rsplit('/', 1)[0] + '/4'

pytestmark = pytest.mark.skipif(not os.getenv('DATABASE_URL'),
                                reason='needs DATABASE_URL and Redis')


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _start_coordinator(tmp_path, port, **extra_env):
    log = open(tmp_path / f'coordinator-{port}.log', 'w')
    env = {**os.environ, 'REDIS_URL': E2E_REDIS_URL,
           'FORUM_DB': str(tmp_path / 'forum.db'), 'ADMIN_KEY': 'e2e-admin',
           'GEMMANET_TASK_TIMEOUT': '5', 'LOG_LEVEL': 'WARNING',
           # the suite opens many node connections from one address
           'GEMMANET_WS_CONNECTS_PER_MINUTE': '1000', **extra_env}
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'gemmanet.coordinator.server:app',
         '--host', '127.0.0.1', '--port', str(port)],
        env=env, stdout=log, stderr=subprocess.STDOUT)
    return proc, log


@pytest.fixture(scope='module')
def base_url(tmp_path_factory):
    redis = pytest.importorskip('redis')
    try:
        redis.Redis.from_url(E2E_REDIS_URL).flushdb()
    except redis.exceptions.ConnectionError:
        pytest.skip('Redis not available')

    with _running_coordinator(tmp_path_factory.mktemp('e2e')) as url:
        yield url


@contextlib.contextmanager
def _running_coordinator(tmp_path, **extra_env):
    port = _free_port()
    proc, log = _start_coordinator(tmp_path, port, **extra_env)
    url = f'http://127.0.0.1:{port}'
    for _ in range(150):
        try:
            if httpx.get(f'{url}/api/v1/status').status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail('coordinator did not start: '
                    + (tmp_path / f'coordinator-{port}.log').read_text())
    try:
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()


@pytest.fixture(scope='module')
def keys(base_url):
    """Three accounts; registration is limited to 5 per hour per IP."""
    def register():
        r = httpx.post(f'{base_url}/api/v1/register', json={})
        assert r.status_code == 200, r.text
        return r.json()
    return {'owner': register(), 'client': register(), 'other': register()}


class RunningNode:
    def __init__(self, base_url, api_key, name, handlers, capabilities=None, **node_kwargs):
        ws_url = base_url.replace('http://', 'ws://') + '/ws/node'
        self.node = Node(name=name, capabilities=capabilities or list(handlers),
                         coordinator_url=ws_url, api_key=api_key, **node_kwargs)
        for task_type, handler in handlers.items():
            self.node.register_handler(task_type, handler)
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.node.start()
        except Exception as e:
            self.error = e

    def wait_registered(self):
        assert self.node._registered.wait(10), f'node did not register: {self.error}'
        return self

    def stop(self):
        self.node.stop()
        self.thread.join(10)


@pytest.fixture
def run_node(base_url, keys):
    started = []

    def start(name, handlers, key='owner', wait=True, **kwargs):
        api_key = keys[key]['api_key'] if key in keys else key
        node = RunningNode(base_url, api_key, name, handlers, **kwargs)
        started.append(node)
        return node.wait_registered() if wait else node

    yield start
    for node in started:
        node.stop()


def client_for(base_url, keys, who='client'):
    return Client(api_key=keys[who]['api_key'], coordinator_url=base_url)


def test_credit_endpoints_are_gone(base_url, keys):
    headers = {'Authorization': f'Bearer {keys["client"]["api_key"]}'}
    assert httpx.get(f'{base_url}/api/v1/balance', headers=headers).status_code == 404
    assert httpx.get(f'{base_url}/api/v1/history', headers=headers).status_code == 404
    assert 'balance' not in keys['client']


def test_node_with_invalid_key_is_rejected(run_node):
    node = run_node('intruder', {'echo': lambda c, **p: c}, key='gn_invalid', wait=False)
    node.thread.join(10)
    assert isinstance(node.error, AuthenticationError)


def test_node_identity_is_stable_and_owned(run_node, base_url):
    first = run_node('stable', {'echo': lambda c, **p: c})
    node_id = first.node.node_id
    first.stop()
    second = run_node('stable', {'echo': lambda c, **p: c})
    assert second.node.node_id == node_id  # reputation survives restarts
    other = run_node('stable', {'echo': lambda c, **p: c}, key='other')
    assert other.node.node_id != node_id   # another account can't claim it


def test_duplicate_node_replaces_old_connection(run_node, base_url):
    old = run_node('dup', {'echo': lambda c, **p: c})
    new = run_node('dup', {'echo': lambda c, **p: c})
    old.thread.join(10)
    assert not old.thread.is_alive()  # the replaced process stops, no flapping
    time.sleep(0.2)
    nodes = httpx.get(f'{base_url}/api/v1/nodes').json()
    assert [n['node_id'] for n in nodes].count(new.node.node_id) == 1


def test_request_roundtrip_and_failed_status(run_node, base_url, keys):
    def boom(content, **params):
        raise RuntimeError('handler exploded')

    node = run_node('worker', {'echo': lambda c, prefix='Echo', **p: f'{prefix}: {c}',
                               'boom': boom})
    with client_for(base_url, keys) as client:
        ok = client.request('echo', 'Hello GemmaNet', params={'prefix': 'E2E'})
        assert ok.status.value == 'completed'
        assert ok.result == 'E2E: Hello GemmaNet'
        assert ok.node_id == node.node.node_id

        failed = client.request('boom', 'x')
        assert failed.status.value == 'failed'
        assert 'handler exploded' in failed.result

    rep = httpx.get(f'{base_url}/api/v1/reputation/{node.node.node_id}').json()
    assert rep['total_tasks'] == 2
    assert rep['success_rate'] == 0.5  # the failure counted against the node


def test_rating_rules(run_node, base_url, keys):
    run_node('rated', {'echo': lambda c, **p: c})
    with client_for(base_url, keys) as client:
        task_id = client.request('echo', 'rate me').task_id
        assert client.rate(task_id, 5)['status'] == 'rated'
        with pytest.raises(GemmaNetError, match='409'):
            client.rate(task_id, 1)
    with client_for(base_url, keys, 'other') as other:
        with pytest.raises(GemmaNetError, match='403'):
            other.rate(task_id, 1)


def test_streaming_is_incremental(run_node, base_url, keys):
    def story(content, **params):
        for word in ('once ', 'upon ', 'a ', 'time'):
            time.sleep(0.2)
            yield word

    run_node('storyteller', {'story': story})
    with client_for(base_url, keys) as client:
        start = time.monotonic()
        pieces, arrivals = [], []
        for piece in client.request_stream('story', 'tell me'):
            pieces.append(piece)
            arrivals.append(time.monotonic() - start)
    assert ''.join(pieces) == 'once upon a time'
    assert len(pieces) == 4
    assert arrivals[0] < 0.6 < arrivals[-1]  # first words arrive before the last


def test_stream_may_outlast_task_timeout_while_producing(run_node, base_url, keys):
    # GEMMANET_TASK_TIMEOUT is 5s here; the stream takes ~6s but never goes
    # silent for long, so it must complete.
    def slow_story(content, **params):
        for i in range(12):
            time.sleep(0.5)
            yield f'{i} '

    run_node('long-story', {'longstory': slow_story})
    with client_for(base_url, keys) as client:
        text = ''.join(client.request_stream('longstory', 'go', timeout=30))
    assert text == ''.join(f'{i} ' for i in range(12))


def test_lost_registry_entry_is_restored_by_heartbeat(run_node, base_url):
    import redis

    node = run_node('resilient', {'echo': lambda c, **p: c}, heartbeat_interval=0.3)
    node_id = node.node.node_id
    redis.Redis.from_url(E2E_REDIS_URL).delete(f'gn:node:{node_id}')
    time.sleep(1)
    nodes = httpx.get(f'{base_url}/api/v1/nodes').json()
    assert node_id in [n['node_id'] for n in nodes]


def test_openai_sdk_multi_turn_usage_and_stream(run_node, base_url, keys):
    openai = pytest.importorskip('openai')

    def chat(content, messages=(), **params):
        last = messages[-1]['content'] if messages else content
        return Completion(f'{len(messages)} turns, last={last}',
                          usage={'prompt_tokens': 11, 'completion_tokens': 4})

    run_node('chatter', {'chat': chat, 'echo': lambda c, **p: f'echo {c}'})
    client = openai.OpenAI(base_url=f'{base_url}/v1', api_key=keys['client']['api_key'])
    messages = [
        {'role': 'system', 'content': 'be nice'},
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'hello'},
        {'role': 'user', 'content': 'and now?'},
    ]
    resp = client.chat.completions.create(model='gemmanet/auto', messages=messages)
    assert resp.choices[0].message.content == '4 turns, last=and now?'
    assert resp.usage.prompt_tokens == 11
    assert resp.usage.total_tokens == 15

    stream = client.chat.completions.create(model='gemmanet/auto', messages=messages,
                                            stream=True)
    text = ''.join(c.choices[0].delta.content or '' for c in stream if c.choices)
    assert text == '4 turns, last=and now?'

    echo = client.chat.completions.create(
        model='gemmanet/echo', messages=[{'role': 'user', 'content': 'x'}])
    assert echo.choices[0].message.content == 'echo x'


def test_split_only_for_whitelisted_tasks(run_node, base_url, keys):
    run_node('splitter', {'echo': lambda c, **p: f'[{c[:5]}]',
                          'translate': lambda c, **p: f'[{c[:5]}]'})
    long_text = '\n\n'.join(f'Paragraph {i}: ' + 'lorem ipsum ' * 20 for i in range(6))
    with client_for(base_url, keys) as client:
        assert client.request('echo', long_text).result.count('[') == 1
        assert client.request('translate', long_text).result.count('[') == 3


def test_node_disconnect_fails_request_fast(run_node, base_url, keys):
    def slow(content, **params):
        time.sleep(3)
        return 'too late'

    node = run_node('flaky', {'slow': slow})
    threading.Timer(0.5, node.stop).start()
    start = time.monotonic()
    r = httpx.post(f'{base_url}/api/v1/request', timeout=10,
                   headers={'Authorization': f'Bearer {keys["client"]["api_key"]}'},
                   json={'task_type': 'slow', 'content': 'x'})
    assert r.status_code == 502
    assert time.monotonic() - start < 2.5  # not the 5s task timeout


def test_feedback_requires_admin_key(base_url):
    assert httpx.post(f'{base_url}/api/v1/feedback',
                      json={'type': 'bug', 'message': 'e2e'}).status_code == 200
    for header in ('Bearer wrong', b'Bearer \xa0'):
        r = httpx.get(f'{base_url}/api/v1/feedback', headers={'Authorization': header})
        assert r.status_code == 401
    r = httpx.get(f'{base_url}/api/v1/feedback', headers={'Authorization': 'Bearer e2e-admin'})
    assert r.status_code == 200
    assert any(f['message'] == 'e2e' for f in r.json())


def test_status_counts_completed_tasks(base_url):
    status = httpx.get(f'{base_url}/api/v1/status').json()
    assert status['version'] == '0.2.0a1'
    assert status['total_tasks_today'] >= 1


def test_second_coordinator_refuses_to_start(base_url, tmp_path):
    port = _free_port()
    proc, log = _start_coordinator(tmp_path, port, GEMMANET_INSTANCE_LOCK_WAIT='1')
    try:
        assert proc.wait(timeout=30) != 0
    finally:
        log.close()
    assert 'Another GemmaNet coordinator' in (tmp_path / f'coordinator-{port}.log').read_text()


# --- Resource limits (docs/design/security-hardening.md, M4) ---

@pytest.fixture(scope='module')
def limited_url(base_url, tmp_path_factory):
    import redis
    redis.Redis.from_url(LIMITS_REDIS_URL).flushdb()
    with _running_coordinator(tmp_path_factory.mktemp('limits'), REDIS_URL=LIMITS_REDIS_URL,
                              GEMMANET_MAX_NODES_PER_ACCOUNT='2',
                              GEMMANET_WS_MAX_MESSAGE_BYTES='65536',
                              GEMMANET_MAX_RESULT_BYTES='1000') as url:
        yield url


@pytest.fixture
def run_limited_node(limited_url, keys):
    started = []

    def start(name, handlers, key='owner', wait=True):
        node = RunningNode(limited_url, keys[key]['api_key'], name, handlers)
        started.append(node)
        return node.wait_registered() if wait else node

    yield start
    for node in started:
        node.stop()


def _online_names(url):
    return sorted(n['name'] for n in httpx.get(f'{url}/api/v1/nodes').json())


def test_request_body_over_2mb_is_refused(base_url, keys):
    headers = {'Authorization': f'Bearer {keys["client"]["api_key"]}',
               'Content-Type': 'application/json'}
    big = json.dumps({'task_type': 'echo', 'content': 'x' * 2_000_000})
    for path in ('/api/v1/request', '/v1/chat/completions', '/api/v1/feedback'):
        assert httpx.post(f'{base_url}{path}', content=big, headers=headers).status_code == 413


def test_node_connections_are_rate_limited_per_ip(base_url, tmp_path):
    import redis
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect
    redis.Redis.from_url(RATE_REDIS_URL).flushdb()
    with _running_coordinator(tmp_path, REDIS_URL=RATE_REDIS_URL,
                              GEMMANET_WS_CONNECTS_PER_MINUTE='3') as url:
        ws_url = url.replace('http://', 'ws://') + '/ws/node'
        for _ in range(3):
            with connect(ws_url):
                pass
        with pytest.raises(InvalidStatus) as refused:
            connect(ws_url)
        assert refused.value.response.status_code == 403
        # The SDK treats a refused handshake as temporary: it keeps retrying
        # with backoff instead of crashing.
        node = RunningNode(url, 'gn_whatever', 'patient', {'echo': lambda c, **p: c})
        time.sleep(2.5)
        assert node.thread.is_alive() and node.error is None
        node.stop()


def test_nodes_per_account_are_capped(run_limited_node, limited_url):
    run_limited_node('cap-1', {'echo': lambda c, **p: c})
    run_limited_node('cap-2', {'echo': lambda c, **p: c})
    third = run_limited_node('cap-3', {'echo': lambda c, **p: c}, wait=False)
    third.thread.join(10)
    assert isinstance(third.error, GemmaNetError) and 'at most 2 nodes' in str(third.error)
    # Reconnecting under the same name replaces the node; it does not count twice.
    run_limited_node('cap-1', {'echo': lambda c, **p: c})
    # Other accounts have their own allowance.
    run_limited_node('cap-other', {'echo': lambda c, **p: c}, key='other')
    time.sleep(0.3)
    assert _online_names(limited_url) == ['cap-1', 'cap-2', 'cap-other']


def test_concurrent_registrations_cannot_overshoot_the_cap(run_limited_node, limited_url):
    racers = [run_limited_node(f'race-{i}', {'echo': lambda c, **p: c}, wait=False)
              for i in range(6)]
    for node in racers:
        node.node._registered.wait(5)
    time.sleep(1)
    assert len([n for n in _online_names(limited_url) if n.startswith('race-')]) == 2


def test_oversized_node_message_closes_the_connection(limited_url, keys):
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    from gemmanet.sdk.models import MsgType, make_ws_msg

    with connect(limited_url.replace('http://', 'ws://') + '/ws/node') as ws:
        ws.send(make_ws_msg(MsgType.NODE_REGISTER, {
            'api_key': keys['owner']['api_key'], 'name': 'chatty', 'capabilities': ['echo']}))
        assert json.loads(ws.recv())['msg_type'] == 'node_registered'
        ws.send(make_ws_msg(MsgType.HEARTBEAT, {'padding': 'x' * 70_000}))
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                ws.recv(timeout=5)
        assert closed.value.rcvd.code == 1009


def test_result_over_the_limit_fails_cleanly(run_limited_node, limited_url, keys):
    def big_stream(content, **params):
        for _ in range(3):
            yield 'y' * 600

    node = run_limited_node('verbose', {
        'fits': lambda c, **p: 'x' * 1000,
        'big': lambda c, **p: 'x' * 1001,
        'bigstream': big_stream,
    })
    headers = {'Authorization': f'Bearer {keys["client"]["api_key"]}'}
    with client_for(limited_url, keys) as client:
        assert client.request('fits', 'go').result == 'x' * 1000
        resp = httpx.post(f'{limited_url}/api/v1/request', headers=headers,
                          json={'task_type': 'big', 'content': 'go'})
        assert resp.status_code == 502 and 'more than 1000 bytes' in resp.text

        resp = httpx.post(f'{limited_url}/api/v1/request', headers=headers, timeout=10,
                          json={'task_type': 'bigstream', 'content': 'go', 'stream': True})
        events = [json.loads(line[6:]) for line in resp.text.splitlines()
                  if line.startswith('data: ')]
        assert events[0] == {'delta': 'y' * 600}
        assert events[-1]['error']['code'] == 'result_too_large'

        resp = httpx.post(f'{limited_url}/v1/chat/completions', headers=headers,
                          json={'model': 'gemmanet/big',
                                'messages': [{'role': 'user', 'content': 'go'}]})
        assert resp.status_code == 502 and resp.json()['error']['code'] == 'result_too_large'

        # The node stays connected and usable.
        assert client.request('fits', 'again').result == 'x' * 1000
    rep = httpx.get(f'{limited_url}/api/v1/reputation/{node.node.node_id}').json()
    assert rep['total_tasks'] == 5 and rep['success_rate'] == 0.4
