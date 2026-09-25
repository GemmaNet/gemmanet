"""FastAPI main app with REST + WebSocket endpoints."""
import asyncio
import hmac
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from gemmanet import __version__
from gemmanet.coordinator.auth import APIKeyManager, Feedback
from gemmanet.coordinator.database import SessionLocal, init_db
from gemmanet.coordinator.instance_lock import SingleInstanceLock
from gemmanet.coordinator.registry import NodeRegistry
from gemmanet.coordinator.reputation import (
    AlreadyRated,
    NotTaskOwner,
    ReputationSystem,
    TaskNotFound,
)
from gemmanet.coordinator.router import RoutingEngine
from gemmanet.coordinator.tasks import (
    NodeDisconnected,
    PendingTask,
    TaskTracker,
    next_event,
    wait_result,
)
from gemmanet.coordinator.ws_manager import WSConnectionManager
from gemmanet.sdk.models import (
    MsgType,
    NodeRegisterPayload,
    TaskResult,
    TaskStatus,
    make_ws_msg,
    parse_ws_msg,
)
from gemmanet.sdk.node import CLOSE_REPLACED

load_dotenv()

logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('gemmanet.coordinator')

ADMIN_KEY = os.getenv('ADMIN_KEY', '')
TASK_TIMEOUT = float(os.getenv('GEMMANET_TASK_TIMEOUT', '60'))
TASK_TIMEOUT_MS = int(TASK_TIMEOUT * 1000)
# A stream may run longer than TASK_TIMEOUT as long as chunks keep arriving
# (TASK_TIMEOUT then bounds the silence between chunks), up to this cap.
STREAM_MAX_SECONDS = float(os.getenv('GEMMANET_STREAM_MAX_SECONDS', '600'))
REGISTER_TIMEOUT = 30
MAX_CONTENT_CHARS = 200_000
DISPATCH_ATTEMPTS = 3
SPLIT_CHUNKS = 3

CLOSE_POLICY_VIOLATION = 1008
CLOSE_AUTH_FAILED = 4001

# Node ids are derived from (account, node name) so a node keeps its
# identity - and reputation - across restarts, and nobody else can claim it.
NODE_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'https://gemmanet.net/node')

BENCHMARK_PROMPTS = [
    'Reply with exactly: Hello GemmaNet',
    'Write a 50-word paragraph about AI.',
    'BENCHMARK_ECHO_TEST',
]
BENCHMARK_TTL = 6 * 3600  # 6 hours
BENCHMARK_REPLY_TIMEOUT = 300  # give up waiting and re-benchmark later


class NoNodeAvailable(Exception):
    pass


def derive_node_id(account_id: str, name: str) -> str:
    return str(uuid.uuid5(NODE_ID_NAMESPACE, f'{account_id}/{name}'))


def _nonneg_int(value, default: int = 0) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _load_info(payload: dict) -> dict:
    """Keep only the load fields of a heartbeat, with sane values."""
    try:
        cpu = float(payload.get('cpu_percent', 0.0))
    except (TypeError, ValueError):
        cpu = 0.0
    if not math.isfinite(cpu):
        cpu = 0.0
    return {
        'cpu_percent': min(max(cpu, 0.0), 100.0),
        'active_tasks': _nonneg_int(payload.get('active_tasks')),
    }


def _usage(payload: dict) -> dict | None:
    usage = payload.get('usage')
    if not isinstance(usage, dict):
        return None
    prompt = _nonneg_int(usage.get('prompt_tokens'))
    completion = _nonneg_int(usage.get('completion_tokens'))
    return {'prompt_tokens': prompt, 'completion_tokens': completion,
            'total_tokens': prompt + completion}


def _sse(data: dict) -> str:
    return f'data: {json.dumps(data)}\n\n'


async def process_benchmark_result(registry: NodeRegistry, node_id: str,
                                   results: list, measured_ms: float):
    """Store a benchmark profile in Redis.

    Timing comes from the coordinator's own round-trip measurement, not the
    node's self-reported numbers, so a node cannot fake a fast profile.
    """
    if not isinstance(results, list):
        results = []
    cleaned = []
    for r in results[:len(BENCHMARK_PROMPTS)]:
        if isinstance(r, dict):
            cleaned.append({
                'prompt': str(r.get('prompt', ''))[:200],
                'response': str(r.get('response', ''))[:500],
                'time_ms': _nonneg_int(r.get('time_ms')),
                'success': bool(r.get('success', False)),
            })

    num = len(cleaned) or 1
    all_passed = bool(cleaned) and all(r['success'] for r in cleaned)
    total_output_len = sum(len(r['response']) for r in cleaned)
    total_time_sec = max(measured_ms / 1000.0, 0.001)

    profile = {
        'node_id': node_id,
        'avg_response_ms': round(measured_ms / num, 1),
        'estimated_tokens_per_sec': round(total_output_len / total_time_sec, 2),
        'benchmark_passed': all_passed,
        'results': cleaned,
        'timestamp': int(time.time()),
    }

    await registry.redis.set(f'gn:bench:{node_id}', json.dumps(profile), ex=BENCHMARK_TTL)
    logger.info(f'Benchmark profile stored for {node_id}: '
                f'avg={profile["avg_response_ms"]:.0f}ms, '
                f'tps={profile["estimated_tokens_per_sec"]}, passed={all_passed}')
    return profile


limiter = Limiter(key_func=get_remote_address)


class RequestBody(BaseModel):
    task_type: str = Field(min_length=1, max_length=64)
    content: str = Field(max_length=MAX_CONTENT_CHARS)
    params: dict = Field(default_factory=dict)
    stream: bool = False
    api_key: str | None = None  # deprecated: send the Authorization header

    @field_validator('params')
    @classmethod
    def _check_params(cls, value: dict) -> dict:
        # params become handler keyword arguments on the node
        for key in value:
            if not key.isidentifier() or key == 'content':
                raise ValueError(f'invalid params key: {key!r}')
        return value


class RegisterBody(BaseModel):
    email: str | None = Field(default=None, max_length=256)


class FeedbackBody(BaseModel):
    type: str
    message: str = Field(max_length=4096)
    email: str | None = Field(default=None, max_length=256)


class RateBody(BaseModel):
    task_id: str = Field(max_length=64)
    rating: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    registry = NodeRegistry(redis_url=redis_url)
    await registry.init()
    instance_lock = SingleInstanceLock(
        registry.redis, wait=float(os.getenv('GEMMANET_INSTANCE_LOCK_WAIT', '20')))
    await instance_lock.acquire()
    init_db()

    ws_manager = WSConnectionManager()
    reputation = ReputationSystem(redis_url=redis_url)
    router = RoutingEngine(registry=registry, ws_manager=ws_manager,
                           reputation=reputation)

    app.state.registry = registry
    app.state.ws_manager = ws_manager
    app.state.reputation = reputation
    app.state.router = router
    # Entries are normally popped by their request; the age limit is a
    # backstop for streams whose response body never started.
    app.state.tracker = TaskTracker(max_age=STREAM_MAX_SECONDS + TASK_TIMEOUT)

    from gemmanet.forum.database import init_forum_db, seed_forum_db
    init_forum_db()
    seed_forum_db()

    logger.info('Coordinator started')
    yield

    await instance_lock.release()
    await reputation.redis.aclose()
    await registry.close()
    logger.info('Coordinator stopped')


app = FastAPI(title='GemmaNet Coordinator', version=__version__, lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

from gemmanet.dashboard.app import dashboard_app  # noqa: E402

app.mount('/dashboard', dashboard_app)

from gemmanet.forum.app import forum_app  # noqa: E402

app.mount('/talk', forum_app)


# --- Global exception handler ---

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error('Unhandled error', exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={'error': 'Internal server error', 'detail': 'An unexpected error occurred'},
    )


# --- Auth ---

def _bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith('Bearer '):
        return None
    return authorization[7:].strip() or None


async def _account_for_key(key: str | None) -> str | None:
    if not key:
        return None
    info = await asyncio.to_thread(APIKeyManager.validate, key)
    return info['account_id'] if info else None


async def require_account(authorization: str | None = Header(default=None)) -> str:
    """Validate the Bearer API key and return the owning account id."""
    account_id = await _account_for_key(_bearer(authorization))
    if not account_id:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')
    return account_id


# --- Task dispatch ---

def _today_key() -> str:
    return 'gn:stats:tasks:' + datetime.now(UTC).strftime('%Y-%m-%d')


async def _count_completed_task():
    key = _today_key()
    pipe = app.state.registry.redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, 2 * 86400)
    await pipe.execute()


_background_tasks: set[asyncio.Task] = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _close_replaced(node_id: str, websocket):
    try:
        await asyncio.wait_for(websocket.close(code=CLOSE_REPLACED), 10)
    except Exception:
        logger.debug(f'Closing replaced connection for {node_id} failed')


async def _drop_connection(node_id: str, websocket):
    if app.state.ws_manager.detach(node_id, websocket):
        await app.state.registry.unregister(node_id)
    app.state.tracker.fail_connection(websocket)


async def _assign(task_id: str, node_id: str, task_type: str, content: str,
                  params: dict, stream: bool) -> PendingTask | None:
    """Send a task to one node; returns None if that node can't take it."""
    ws = app.state.ws_manager.get(node_id)
    if ws is None:
        return None
    # Register before sending so a fast reply can never arrive first.
    task = app.state.tracker.create(task_id, node_id, ws)
    message = make_ws_msg(MsgType.TASK_ASSIGN, {
        'task_id': task_id,
        'task_type': task_type,
        'content': content,
        'params': params,
        'stream': stream,
    })
    if await app.state.ws_manager.send(node_id, ws, message):
        return task
    app.state.tracker.pop(task_id)
    await _drop_connection(node_id, ws)
    return None


async def _dispatch(task_id: str, task_type: str, content: str, params: dict,
                    stream: bool = False) -> PendingTask:
    """Assign a task to the best node, falling back to the next best."""
    tried: set[str] = set()
    for _ in range(DISPATCH_ATTEMPTS):
        node_id = await app.state.router.find_best_node(task_type, params, exclude=tried)
        if node_id is None:
            break
        tried.add(node_id)
        task = await _assign(task_id, node_id, task_type, content, params, stream)
        if task is not None:
            return task
    raise NoNodeAvailable(task_type)


async def _record_outcome(task_id: str, requester: str,
                          outcomes: list[tuple[str, bool, int]]):
    """Feed (node_id, success, elapsed_ms) into reputation and stats."""
    reputation: ReputationSystem = app.state.reputation
    for node_id, success, elapsed_ms in outcomes:
        await reputation.record_task_result(node_id, success=success,
                                            response_time_ms=elapsed_ms)
        await reputation.check_and_suspend(node_id)
    await reputation.remember_task(task_id, requester, sorted({n for n, _, _ in outcomes}))
    if outcomes and all(success for _, success, _ in outcomes):
        await _count_completed_task()


def _task_result(task: PendingTask, payload: dict) -> TaskResult:
    succeeded = payload.get('status') == TaskStatus.COMPLETED.value
    return TaskResult(
        task_id=task.task_id,
        status=TaskStatus.COMPLETED if succeeded else TaskStatus.FAILED,
        result=str(payload.get('result', '')),
        node_id=task.node_id,
        processing_time_ms=_nonneg_int(payload.get('processing_time_ms'), task.elapsed_ms),
        usage=_usage(payload),
    )


async def _run_task(task_id: str, requester: str, task_type: str, content: str,
                    params: dict) -> TaskResult:
    """Run a task to completion (splitting it when appropriate)."""
    if app.state.router.should_split(content, task_type):
        return await _run_split_task(task_id, requester, task_type, content, params)

    task = await _dispatch(task_id, task_type, content, params)
    deadline = asyncio.get_running_loop().time() + TASK_TIMEOUT
    try:
        payload = await wait_result(task, deadline)
    except TimeoutError:
        await _record_outcome(task_id, requester, [(task.node_id, False, TASK_TIMEOUT_MS)])
        raise
    except NodeDisconnected:
        await _record_outcome(task_id, requester, [(task.node_id, False, task.elapsed_ms)])
        raise
    finally:
        app.state.tracker.pop(task_id)

    result = _task_result(task, payload)
    await _record_outcome(task_id, requester, [
        (task.node_id, result.status == TaskStatus.COMPLETED, task.elapsed_ms)])
    logger.info(f'Task finished: task_id={task_id}, node_id={task.node_id}, '
                f'status={result.status.value}, time_ms={task.elapsed_ms}')
    return result


async def _run_split_task(task_id: str, requester: str, task_type: str,
                          content: str, params: dict) -> TaskResult:
    router: RoutingEngine = app.state.router
    chunks = router.split_content(content, SPLIT_CHUNKS)
    node_ids = await router.find_nodes_for_split(task_type, len(chunks))
    if not node_ids:
        raise NoNodeAvailable(task_type)

    loop = asyncio.get_running_loop()
    started = loop.time()
    tasks: list[PendingTask] = []
    try:
        for i, chunk in enumerate(chunks):
            sub_id = f'{task_id}-{i}'
            start = i % len(node_ids)
            task = None
            for node_id in node_ids[start:] + node_ids[:start]:
                task = await _assign(sub_id, node_id, task_type, chunk, params, False)
                if task is not None:
                    break
            if task is None:
                task = await _dispatch(sub_id, task_type, chunk, params)
            tasks.append(task)

        deadline = loop.time() + TASK_TIMEOUT
        outcomes = await asyncio.gather(
            *(wait_result(t, deadline) for t in tasks), return_exceptions=True)
    finally:
        for t in tasks:
            app.state.tracker.pop(t.task_id)

    records, texts = [], []
    error: Exception | None = None
    for task, outcome in zip(tasks, outcomes, strict=True):
        if isinstance(outcome, dict):
            ok = outcome.get('status') == TaskStatus.COMPLETED.value
            records.append((task.node_id, ok, task.elapsed_ms))
            texts.append(str(outcome.get('result', '')))
        elif isinstance(outcome, asyncio.TimeoutError):
            records.append((task.node_id, False, TASK_TIMEOUT_MS))
            error = error or outcome
        elif isinstance(outcome, NodeDisconnected):
            records.append((task.node_id, False, task.elapsed_ms))
            error = error or outcome
        else:
            raise outcome
    await _record_outcome(task_id, requester, records)
    if error is not None:
        raise error

    succeeded = all(ok for _, ok, _ in records)
    elapsed = int((loop.time() - started) * 1000)
    logger.info(f'Split task finished: task_id={task_id}, chunks={len(chunks)}, '
                f'succeeded={succeeded}, time_ms={elapsed}')
    return TaskResult(
        task_id=task_id,
        status=TaskStatus.COMPLETED if succeeded else TaskStatus.FAILED,
        result=router.merge_results(texts),
        processing_time_ms=elapsed,
    )


async def _stream_task(task: PendingTask, requester: str):
    """Relay a streaming task.

    Yields ('delta', text) while the node streams, then exactly one of
    ('done', TaskResult) or ('error', (code, message)). Records reputation
    and always releases the task.
    """
    loop = asyncio.get_running_loop()
    hard_deadline = loop.time() + STREAM_MAX_SECONDS
    deadline = min(loop.time() + TASK_TIMEOUT, hard_deadline)
    streamed = False
    try:
        while True:
            try:
                kind, value = await next_event(task, deadline)
            except TimeoutError:
                await _record_outcome(task.task_id, requester,
                                      [(task.node_id, False, TASK_TIMEOUT_MS)])
                yield 'error', ('timeout', 'Task timed out')
                return
            if kind == 'chunk':
                streamed = True
                deadline = min(loop.time() + TASK_TIMEOUT, hard_deadline)
                yield 'delta', value
                continue
            if kind == 'error':
                await _record_outcome(task.task_id, requester,
                                      [(task.node_id, False, task.elapsed_ms)])
                yield 'error', ('node_disconnected',
                                'Node disconnected before returning a result')
                return

            result = _task_result(task, value)
            succeeded = result.status == TaskStatus.COMPLETED
            await _record_outcome(task.task_id, requester,
                                  [(task.node_id, succeeded, task.elapsed_ms)])
            if not succeeded:
                yield 'error', ('task_failed', result.result or 'Task failed')
                return
            if not streamed and result.result:
                # Handler returned its whole answer at once.
                yield 'delta', result.result
            yield 'done', result
            return
    finally:
        app.state.tracker.pop(task.task_id)


async def _native_sse(task: PendingTask, requester: str):
    async for kind, value in _stream_task(task, requester):
        if kind == 'delta':
            yield _sse({'delta': value})
        elif kind == 'done':
            yield _sse({'done': True, 'result': value.model_dump(mode='json')})
        else:
            code, message = value
            yield _sse({'error': {'code': code, 'message': message},
                        'task_id': task.task_id})


# --- Routes ---

@app.get('/')
async def root_redirect():
    return RedirectResponse(url='/dashboard/')


@app.post('/api/v1/register')
@limiter.limit('5/hour')
async def register(request: Request, body: RegisterBody | None = None):
    if body is None:
        body = RegisterBody()
    try:
        result = await asyncio.to_thread(APIKeyManager.register, body.email)
    except Exception:
        logger.error('Registration failed', exc_info=True)
        raise HTTPException(status_code=500, detail='An unexpected error occurred') from None
    logger.info(f'API key registered: account_id={result["account_id"]}')
    return result


@app.post('/api/v1/feedback')
@limiter.limit('10/hour')
async def submit_feedback(request: Request, body: FeedbackBody,
                          authorization: str | None = Header(default=None)):
    account_id = await _account_for_key(_bearer(authorization))

    if body.type not in ('bug', 'feature', 'other'):
        raise HTTPException(status_code=400, detail='Invalid feedback type')
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail='Message is required')

    def save() -> int:
        with SessionLocal() as session:
            try:
                fb = Feedback(
                    account_id=account_id,
                    feedback_type=body.type,
                    message=body.message.strip(),
                    email=body.email,
                    status='new',
                )
                session.add(fb)
                session.commit()
                session.refresh(fb)
                return fb.id
            except Exception:
                session.rollback()
                raise

    fb_id = await asyncio.to_thread(save)
    logger.info(f'Feedback submitted: id={fb_id}, type={body.type}, '
                f'account_id={account_id or "anonymous"}')
    return {'id': fb_id, 'status': 'received'}


@app.get('/api/v1/feedback')
@limiter.limit('120/minute')
async def list_feedback(request: Request, authorization: str | None = Header(default=None)):
    key = _bearer(authorization)
    # Without a configured ADMIN_KEY nobody may read feedback.
    if not ADMIN_KEY or not key or not hmac.compare_digest(key.encode(), ADMIN_KEY.encode()):
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    def load() -> list[dict]:
        with SessionLocal() as session:
            entries = session.query(Feedback).order_by(Feedback.created_at.desc()).all()
            return [{
                'id': f.id,
                'account_id': f.account_id,
                'type': f.feedback_type,
                'message': f.message,
                'email': f.email,
                'status': f.status,
                'created_at': str(f.created_at) if f.created_at else None,
            } for f in entries]

    return await asyncio.to_thread(load)


@app.websocket('/ws/node')
async def node_websocket(websocket: WebSocket):
    ws_manager: WSConnectionManager = app.state.ws_manager
    registry: NodeRegistry = app.state.registry
    tracker: TaskTracker = app.state.tracker
    loop = asyncio.get_running_loop()
    node_id = None

    async def reject(code: str, message: str, close_code: int):
        await websocket.send_text(make_ws_msg(MsgType.ERROR, {'code': code, 'message': message}))
        await websocket.close(code=close_code)

    try:
        await websocket.accept()
        raw = await asyncio.wait_for(websocket.receive_text(), REGISTER_TIMEOUT)
        try:
            msg = parse_ws_msg(raw)
            if msg.msg_type != MsgType.NODE_REGISTER:
                raise ValueError('first message must be node_register')
            reg = NodeRegisterPayload.model_validate(msg.payload)
        except (ValidationError, ValueError) as e:
            await reject('invalid_registration', str(e)[:500], CLOSE_POLICY_VIOLATION)
            return

        account_id = await _account_for_key(reg.api_key)
        if not account_id:
            await reject('auth_failed', 'Invalid API key', CLOSE_AUTH_FAILED)
            return

        node_id = derive_node_id(account_id, reg.name)
        info = {
            'node_id': node_id,
            'name': reg.name,
            'capabilities': reg.capabilities,
            'languages': reg.languages,
            'model_info': reg.model_info,
        }
        # Acknowledge before the node becomes routable, so the first message
        # it sees is always node_registered, never a task.
        await websocket.send_text(make_ws_msg(MsgType.NODE_REGISTERED, {'node_id': node_id}))
        previous = ws_manager.attach(node_id, websocket, info)
        await registry.register(node_id, info)
        if previous is not None:
            tracker.fail_connection(previous)
            # A half-open socket can take a while to close; don't hold up
            # the new connection for it.
            _spawn(_close_replaced(node_id, previous))

        bench_sent_at: float | None = None

        async def send_benchmark():
            nonlocal bench_sent_at
            bench_msg = make_ws_msg(MsgType.BENCHMARK, {'prompts': BENCHMARK_PROMPTS})
            if await ws_manager.send(node_id, websocket, bench_msg):
                bench_sent_at = loop.time()
                logger.info(f'Benchmark sent to node {node_id}')

        await send_benchmark()

        while True:
            raw = await websocket.receive_text()
            try:
                msg = parse_ws_msg(raw)
            except ValidationError:
                logger.debug(f'Ignoring malformed message from {node_id}')
                continue
            payload = msg.payload

            if msg.msg_type == MsgType.HEARTBEAT:
                load = _load_info(payload)
                if not await registry.update_heartbeat(node_id, load):
                    # Entry expired or Redis lost it while the node stayed
                    # connected: put it back so routing can find it again.
                    await registry.register(node_id, {**info, **load})
                if (bench_sent_at is not None
                        and loop.time() - bench_sent_at > BENCHMARK_REPLY_TIMEOUT):
                    bench_sent_at = None
                if bench_sent_at is None:
                    bench_data = await registry.redis.get(f'gn:bench:{node_id}')
                    if not bench_data or (time.time() - json.loads(bench_data).get(
                            'timestamp', 0) > BENCHMARK_TTL):
                        await send_benchmark()

            elif msg.msg_type == MsgType.BENCHMARK_RESULT:
                if bench_sent_at is None:
                    continue  # unsolicited
                measured_ms = (loop.time() - bench_sent_at) * 1000
                bench_sent_at = None
                await process_benchmark_result(registry, node_id,
                                               payload.get('results', []), measured_ms)

            elif msg.msg_type == MsgType.TASK_CHUNK:
                tracker.add_chunk(str(payload.get('task_id', '')), websocket,
                                  str(payload.get('delta', '')))

            elif msg.msg_type == MsgType.TASK_RESULT:
                tracker.resolve(str(payload.get('task_id', '')), websocket, payload)

    except (TimeoutError, WebSocketDisconnect):
        pass
    except Exception as e:
        logger.error(f'WebSocket error for node {node_id}: {e}')
    finally:
        if node_id:
            await _drop_connection(node_id, websocket)


@app.post('/api/v1/request')
@limiter.limit('60/minute')
async def handle_request(request: Request, body: RequestBody,
                         authorization: str | None = Header(default=None)):
    requester = await _account_for_key(_bearer(authorization) or body.api_key)
    if not requester:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    task_id = str(uuid.uuid4())
    logger.info(f'Task request: task_id={task_id}, task_type={body.task_type}, '
                f'account_id={requester}, stream={body.stream}')
    router: RoutingEngine = app.state.router

    try:
        if body.stream and not router.should_split(body.content, body.task_type):
            task = await _dispatch(task_id, body.task_type, body.content,
                                   body.params, stream=True)
            return StreamingResponse(_native_sse(task, requester),
                                     media_type='text/event-stream')

        result = await _run_task(task_id, requester, body.task_type,
                                 body.content, body.params)
    except NoNodeAvailable:
        raise HTTPException(status_code=404,
                            detail='No node available for this task type') from None
    except TimeoutError:
        raise HTTPException(status_code=504, detail='Task timed out') from None
    except NodeDisconnected:
        raise HTTPException(status_code=502,
                            detail='Node disconnected before returning a result') from None

    if not body.stream:
        return result.model_dump(mode='json')

    # Split tasks are not streamed chunk by chunk; send the merged result.
    async def split_sse():
        if result.status == TaskStatus.COMPLETED:
            yield _sse({'delta': result.result})
            yield _sse({'done': True, 'result': result.model_dump(mode='json')})
        else:
            yield _sse({'error': {'code': 'task_failed', 'message': result.result},
                        'task_id': task_id})

    return StreamingResponse(split_sse(), media_type='text/event-stream')


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return ''.join(part['text'] for part in self.content
                           if isinstance(part, dict) and part.get('type') == 'text'
                           and isinstance(part.get('text'), str))
        return ''


class ChatCompletionRequest(BaseModel):
    model: str = 'gemmanet/auto'
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


def _openai_error(message: str, error_type: str, code: str, status_code: int):
    return JSONResponse(
        status_code=status_code,
        content={'error': {'message': message, 'type': error_type, 'code': code}},
    )


def _parse_model_to_task_type(model: str) -> str:
    """'gemmanet/<capability>' selects a capability; anything else is chat."""
    model = model.strip()
    if model.lower().startswith('gemmanet/'):
        capability = model.split('/', 1)[1]
        return 'chat' if capability.lower() in ('', 'auto') else capability
    return 'chat'


def _messages_to_content(messages: list[dict]) -> str:
    """Flatten the conversation for handlers that only read `content`."""
    system_parts = [m['content'] for m in messages if m['role'] == 'system']
    user_parts = [m['content'] for m in messages if m['role'] == 'user']
    last_user = user_parts[-1] if user_parts else ''
    if system_parts:
        return f'System: {system_parts[-1]}\n\nUser: {last_user}'
    return last_user


async def _openai_sse(task: PendingTask, requester: str, model: str):
    completion_id = f'chatcmpl-{task.task_id}'
    created = int(time.time())

    def chunk(delta: dict, finish_reason: str | None = None) -> str:
        return _sse({
            'id': completion_id,
            'object': 'chat.completion.chunk',
            'created': created,
            'model': model,
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
        })

    yield chunk({'role': 'assistant'})
    async for kind, value in _stream_task(task, requester):
        if kind == 'delta':
            yield chunk({'content': value})
        elif kind == 'done':
            yield chunk({}, 'stop')
        else:
            code, message = value
            yield _sse({'error': {'message': message, 'type': 'server_error', 'code': code}})
    yield 'data: [DONE]\n\n'


@app.post('/v1/chat/completions')
@limiter.limit('60/minute')
async def openai_chat_completions(request: Request, body: ChatCompletionRequest,
                                  authorization: str | None = Header(default=None)):
    requester = await _account_for_key(_bearer(authorization))
    if not requester:
        return _openai_error('Invalid API key', 'authentication_error', 'invalid_api_key', 401)

    messages = [{'role': m.role, 'content': m.text()} for m in body.messages]
    if sum(len(m['content']) for m in messages) > MAX_CONTENT_CHARS:
        return _openai_error('Messages too long', 'invalid_request_error',
                             'context_length_exceeded', 400)

    task_type = _parse_model_to_task_type(body.model)
    content = _messages_to_content(messages)
    # Full conversation for chat-aware handlers (e.g. OllamaHandler).
    params: dict = {'messages': messages}
    if body.max_tokens is not None:
        params['max_tokens'] = body.max_tokens
    if body.temperature is not None:
        params['temperature'] = body.temperature
    task_id = str(uuid.uuid4())

    try:
        if body.stream and not app.state.router.should_split(content, task_type):
            task = await _dispatch(task_id, task_type, content, params, stream=True)
            return StreamingResponse(_openai_sse(task, requester, body.model),
                                     media_type='text/event-stream')
        result = await _run_task(task_id, requester, task_type, content, params)
    except NoNodeAvailable:
        return _openai_error(f'No node available for task type: {task_type}',
                             'server_error', 'no_node_available', 503)
    except TimeoutError:
        return _openai_error('Request timed out', 'server_error', 'timeout', 504)
    except NodeDisconnected:
        return _openai_error('Node disconnected before returning a result',
                             'server_error', 'node_disconnected', 502)

    if result.status != TaskStatus.COMPLETED:
        return _openai_error(result.result or 'Task failed', 'server_error', 'node_error', 502)

    usage = result.usage or {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    message = {'role': 'assistant', 'content': result.result}
    if body.stream:
        async def split_stream():
            completion = {'id': f'chatcmpl-{task_id}', 'object': 'chat.completion.chunk',
                          'created': int(time.time()), 'model': body.model}
            yield _sse({**completion, 'choices': [
                {'index': 0, 'delta': message, 'finish_reason': 'stop'}]})
            yield 'data: [DONE]\n\n'
        return StreamingResponse(split_stream(), media_type='text/event-stream')

    return {
        'id': f'chatcmpl-{task_id}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': body.model,
        'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
        'usage': usage,
    }


@app.get('/v1/models')
@limiter.limit('120/minute')
async def openai_list_models(request: Request):
    registry: NodeRegistry = app.state.registry
    online_nodes = await registry.get_online_nodes()

    capabilities = set()
    for node in online_nodes:
        if isinstance(node, dict):
            for cap in node.get('capabilities', []):
                capabilities.add(cap)

    models = [
        {'id': 'gemmanet/auto', 'object': 'model', 'owned_by': 'gemmanet'},
    ]
    for cap in sorted(capabilities):
        model_id = f'gemmanet/{cap}'
        if model_id != 'gemmanet/auto':
            models.append({'id': model_id, 'object': 'model', 'owned_by': 'gemmanet'})

    static_caps = ['chat', 'translate', 'summarize', 'code']
    for cap in static_caps:
        model_id = f'gemmanet/{cap}'
        if not any(m['id'] == model_id for m in models):
            models.append({'id': model_id, 'object': 'model', 'owned_by': 'gemmanet'})

    return {'object': 'list', 'data': models}


@app.get('/api/v1/status')
@limiter.limit('120/minute')
async def status(request: Request):
    tasks_today = await app.state.registry.redis.get(_today_key())
    return {
        'status': 'running',
        'version': __version__,
        'online_nodes': app.state.ws_manager.online_count,
        'total_tasks_today': int(tasks_today or 0),
    }


@app.get('/api/v1/nodes')
@limiter.limit('120/minute')
async def list_nodes(request: Request, capability: str | None = Query(default=None)):
    registry: NodeRegistry = app.state.registry
    if capability:
        nodes = await registry.get_nodes_by_capability(capability)
    else:
        nodes = await registry.get_online_nodes()
    return nodes


@app.post('/api/v1/rate')
@limiter.limit('60/minute')
async def rate_task(request: Request, body: RateBody,
                    account_id: str = Depends(require_account)):
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail='Rating must be 1-5')

    reputation: ReputationSystem = app.state.reputation
    try:
        node_ids = await reputation.rate_task(body.task_id, account_id, body.rating)
    except TaskNotFound:
        raise HTTPException(status_code=404, detail='Task not found or expired') from None
    except NotTaskOwner:
        raise HTTPException(status_code=403,
                            detail='Only the account that requested a task can rate it') from None
    except AlreadyRated:
        raise HTTPException(status_code=409, detail='Task already rated') from None
    return {'status': 'rated', 'node_ids': node_ids, 'rating': body.rating}


@app.get('/api/v1/reputation/{node_id}')
@limiter.limit('120/minute')
async def get_reputation(request: Request, node_id: str):
    reputation: ReputationSystem = app.state.reputation
    stats = await reputation.get_stats(node_id)
    return {'node_id': node_id, **stats}


@app.get('/api/v1/leaderboard')
@limiter.limit('120/minute')
async def get_leaderboard(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    reputation: ReputationSystem = app.state.reputation
    entries = await reputation.get_leaderboard(limit=limit)
    for entry in entries:
        info = app.state.ws_manager.get_node_info(entry['node_id'])
        entry['name'] = info['name'] if info else None
    return entries


@app.get('/api/v1/benchmark/{node_id}')
@limiter.limit('120/minute')
async def get_benchmark(request: Request, node_id: str):
    registry: NodeRegistry = app.state.registry
    data = await registry.redis.get(f'gn:bench:{node_id}')
    if not data:
        return {'node_id': node_id, 'benchmark': None}
    return {'node_id': node_id, 'benchmark': json.loads(data)}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=os.getenv('COORDINATOR_HOST', '0.0.0.0'),
                port=int(os.getenv('COORDINATOR_PORT', '8800')))
