"""FastAPI main app with REST + WebSocket endpoints."""
import os
import asyncio
import logging
import json
import uuid
import time
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect,
                     HTTPException, Depends, Header, Query, Request)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

from gemmanet.coordinator.ws_manager import WSConnectionManager
from gemmanet.coordinator.registry import NodeRegistry
from gemmanet.coordinator.router import RoutingEngine
from gemmanet.coordinator.reputation import ReputationSystem
from gemmanet.coordinator.auth import APIKeyManager, Feedback
from gemmanet.credits.service import CreditService
from gemmanet.credits.database import init_db, SessionLocal
from gemmanet.sdk.models import (
    MsgType, TaskResult, TaskStatus, NodeInfo,
    make_ws_msg, parse_ws_msg,
)

logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('gemmanet.coordinator')

ADMIN_KEY = os.getenv('ADMIN_KEY', '')

BENCHMARK_PROMPTS = [
    'Reply with exactly: Hello GemmaNet',
    'Write a 50-word paragraph about AI.',
    'BENCHMARK_ECHO_TEST',
]
BENCHMARK_TTL = 6 * 3600  # 6 hours


async def send_benchmark(ws_manager: WSConnectionManager, node_id: str):
    """Send benchmark prompts to a node."""
    bench_msg = make_ws_msg(MsgType.BENCHMARK, {'prompts': BENCHMARK_PROMPTS})
    try:
        await ws_manager.send_to_node(node_id, bench_msg)
        logger.info(f'Benchmark sent to node {node_id}')
    except Exception as e:
        logger.warning(f'Failed to send benchmark to {node_id}: {e}')


async def process_benchmark_result(registry: NodeRegistry, node_id: str,
                                    results: list[dict]):
    """Process benchmark results and store profile in Redis."""
    total_time = 0
    total_output_len = 0
    all_passed = True

    for r in results:
        total_time += r.get('time_ms', 0)
        total_output_len += len(r.get('response', ''))
        if not r.get('success', False):
            all_passed = False

    num = len(results) or 1
    avg_response_ms = total_time / num
    total_time_sec = total_time / 1000.0 if total_time > 0 else 1.0
    estimated_tokens_per_sec = round(total_output_len / total_time_sec, 2)

    profile = {
        'node_id': node_id,
        'avg_response_ms': round(avg_response_ms, 1),
        'estimated_tokens_per_sec': estimated_tokens_per_sec,
        'benchmark_passed': all_passed,
        'results': results,
        'timestamp': int(time.time()),
    }

    await registry.redis.setex(
        f'gn:bench:{node_id}', BENCHMARK_TTL, json.dumps(profile))
    logger.info(f'Benchmark profile stored for {node_id}: '
                f'avg={avg_response_ms:.0f}ms, tps={estimated_tokens_per_sec}, '
                f'passed={all_passed}')
    return profile


limiter = Limiter(key_func=get_remote_address)


class RequestBody(BaseModel):
    task_type: str
    content: str
    params: dict = {}
    max_cost: int | None = None
    api_key: str | None = None


class RegisterBody(BaseModel):
    email: Optional[str] = None


class FeedbackBody(BaseModel):
    type: str
    message: str
    email: Optional[str] = None


class RateBody(BaseModel):
    task_id: str
    rating: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    registry = NodeRegistry(redis_url=redis_url)
    await registry.init()
    init_db()

    ws_manager = WSConnectionManager()
    credit_service = CreditService()
    reputation = ReputationSystem(redis_url=redis_url)
    router = RoutingEngine(registry=registry, ws_manager=ws_manager,
                           reputation=reputation)

    app.state.registry = registry
    app.state.ws_manager = ws_manager
    app.state.credit_service = credit_service
    app.state.reputation = reputation
    app.state.router = router
    app.state.pending_tasks = {}
    app.state.total_tasks_today = 0

    from gemmanet.forum.database import init_forum_db, seed_forum_db
    init_forum_db()
    seed_forum_db()

    logger.info('Coordinator started')
    yield

    await registry.close()
    logger.info('Coordinator stopped')


app = FastAPI(title='GemmaNet Coordinator', version='0.1.0a1', lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

from gemmanet.dashboard.app import dashboard_app
app.mount('/dashboard', dashboard_app)

from gemmanet.forum.app import forum_app
app.mount('/talk', forum_app)


# --- Global exception handler ---

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f'Unhandled error: {exc}', exc_info=True)
    return JSONResponse(
        status_code=500,
        content={'error': 'Internal server error', 'detail': 'An unexpected error occurred'},
    )


# --- Auth dependency ---

async def verify_api_key(authorization: str | None = Header(default=None)) -> str:
    """Validate API key and return the associated node_id."""
    if not authorization:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Invalid or missing API key')
    key = authorization[7:].strip()
    if not key:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    result = APIKeyManager.validate(key)
    if not result:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    return result['node_id']


# --- Routes ---

@app.get('/')
async def root_redirect():
    return RedirectResponse(url='/dashboard/')


@app.post('/api/v1/register')
@limiter.limit('5/hour')
async def register(request: Request, body: RegisterBody = None):
    if body is None:
        body = RegisterBody()
    try:
        result = APIKeyManager.register(email=body.email)
        logger.info(f'API key registered: node_id={result["node_id"]}, email={body.email or "none"}')
        return result
    except Exception:
        logger.error('Registration failed', exc_info=True)
        raise HTTPException(status_code=500, detail='An unexpected error occurred')


@app.post('/api/v1/feedback')
@limiter.limit('10/hour')
async def submit_feedback(request: Request, body: FeedbackBody,
                          authorization: str = Header(default=None)):
    node_id = None
    if authorization and authorization.startswith('Bearer '):
        key = authorization[7:].strip()
        if key:
            result = APIKeyManager.validate(key)
            if result:
                node_id = result['node_id']

    if body.type not in ('bug', 'feature', 'other'):
        raise HTTPException(status_code=400, detail='Invalid feedback type')
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail='Message is required')

    with SessionLocal() as session:
        try:
            fb = Feedback(
                node_id=node_id,
                feedback_type=body.type,
                message=body.message.strip(),
                email=body.email,
                status='new',
            )
            session.add(fb)
            session.commit()
            session.refresh(fb)
            fb_id = fb.id
        except Exception:
            session.rollback()
            raise

    logger.info(f'Feedback submitted: id={fb_id}, type={body.type}, node_id={node_id or "anonymous"}')
    return {'id': fb_id, 'status': 'received'}


@app.get('/api/v1/feedback')
@limiter.limit('120/minute')
async def list_feedback(request: Request, authorization: str | None = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Invalid or missing API key')
    key = authorization[7:].strip()
    if key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    with SessionLocal() as session:
        entries = session.query(Feedback).order_by(Feedback.created_at.desc()).all()
        return [{
            'id': f.id,
            'node_id': f.node_id,
            'type': f.feedback_type,
            'message': f.message,
            'email': f.email,
            'status': f.status,
            'created_at': str(f.created_at) if f.created_at else None,
        } for f in entries]


@app.websocket('/ws/node')
async def node_websocket(websocket: WebSocket):
    ws_manager: WSConnectionManager = app.state.ws_manager
    registry: NodeRegistry = app.state.registry
    credit_service: CreditService = app.state.credit_service
    node_id = None

    try:
        await websocket.accept()
        raw = await websocket.receive_text()
        msg = parse_ws_msg(raw)
        if msg.msg_type != MsgType.NODE_REGISTER:
            await websocket.close(code=1008)
            return

        payload = msg.payload
        node_id = payload['node_id']

        # Store connection (already accepted above)
        ws_manager.connections[node_id] = websocket
        logger.info(f'Node connected: {node_id}')
        ws_manager.register_node(node_id, payload)
        await registry.register(node_id, payload)

        try:
            credit_service.create_account(node_id, initial_balance=1000)
        except Exception:
            pass

        balance = credit_service.get_balance(node_id)
        credit_msg = make_ws_msg(MsgType.CREDIT_UPDATE, {
            'node_id': node_id, 'balance': balance, 'change': 0, 'reason': 'connected',
        })
        await ws_manager.send_to_node(node_id, credit_msg)

        # Send initial benchmark
        await send_benchmark(ws_manager, node_id)

        while True:
            raw = await websocket.receive_text()
            msg = parse_ws_msg(raw)

            if msg.msg_type == MsgType.HEARTBEAT:
                load_info = msg.payload
                await registry.update_heartbeat(node_id, load_info)

                # Re-benchmark if last bench is older than 6 hours
                bench_data = await registry.redis.get(f'gn:bench:{node_id}')
                if not bench_data:
                    await send_benchmark(ws_manager, node_id)
                else:
                    bench_profile = json.loads(bench_data)
                    if time.time() - bench_profile.get('timestamp', 0) > BENCHMARK_TTL:
                        await send_benchmark(ws_manager, node_id)

            elif msg.msg_type == MsgType.BENCHMARK_RESULT:
                results = msg.payload.get('results', [])
                await process_benchmark_result(registry, node_id, results)

            elif msg.msg_type == MsgType.TASK_RESULT:
                task_id = msg.payload.get('task_id')
                if task_id and task_id in app.state.pending_tasks:
                    future = app.state.pending_tasks[task_id]
                    if not future.done():
                        future.set_result(msg.payload)
                    logger.info(f'Task completed: task_id={task_id}, node_id={node_id}')

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f'WebSocket error for node {node_id}: {e}')
    finally:
        if node_id:
            await ws_manager.disconnect(node_id)
            await registry.unregister(node_id)
            logger.info(f'Node disconnected: {node_id}')


@app.post('/api/v1/request')
@limiter.limit('60/minute')
async def handle_request(request: Request, body: RequestBody):
    key = body.api_key
    if not key or not key.strip():
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    # Validate the API key
    key_info = APIKeyManager.validate(key)
    if not key_info:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')

    client_id = key_info['node_id']
    credit_service: CreditService = app.state.credit_service
    router: RoutingEngine = app.state.router
    ws_manager: WSConnectionManager = app.state.ws_manager

    logger.info(f'Task request: task_type={body.task_type}, node_id={client_id}')

    task_id = str(uuid.uuid4())
    should_split = router.should_split(body.content, body.task_type)

    if should_split:
        chunks = router.split_content(body.content, 3)
        num_chunks = len(chunks)
    else:
        num_chunks = 1

    cost = 10 * num_chunks

    if body.max_cost is not None and cost > body.max_cost:
        raise HTTPException(status_code=402, detail='Insufficient credits')

    balance = credit_service.get_balance(client_id)
    if balance < cost:
        raise HTTPException(status_code=402, detail='Insufficient credits')

    reputation: ReputationSystem = app.state.reputation

    if not should_split:
        node_id = await router.find_best_node(body.task_type, body.params)
        if not node_id:
            raise HTTPException(status_code=404, detail='No node available for this task type')

        if not credit_service.charge(client_id, cost, task_id):
            raise HTTPException(status_code=402, detail='Insufficient credits')

        charged = True
        charged_amount = cost

        try:
            # Store task -> node mapping for rating
            await reputation.redis.setex(f'gn:task:node:{task_id}', 3600, node_id)

            future = asyncio.get_event_loop().create_future()
            app.state.pending_tasks[task_id] = future

            assign_msg = make_ws_msg(MsgType.TASK_ASSIGN, {
                'task_id': task_id,
                'task_type': body.task_type,
                'content': body.content,
                'params': body.params,
                'reward': 10,
            })
            success = await ws_manager.send_to_node(node_id, assign_msg)
            if not success:
                await registry.unregister(node_id)
                node_id = await router.find_best_node(body.task_type, body.params)
                if node_id:
                    success = await ws_manager.send_to_node(node_id, assign_msg)
                if not success:
                    raise HTTPException(status_code=404, detail='No available node')

            start = time.time()
            result_payload = await asyncio.wait_for(future, timeout=60.0)
            elapsed = int((time.time() - start) * 1000)

            credit_service.reward(node_id, 10, task_id)
            charged = False  # credits legitimately spent
            app.state.total_tasks_today += 1

            await reputation.record_task_result(node_id, success=True,
                                                response_time_ms=elapsed)
            await reputation.check_and_suspend(node_id)

            logger.info(f'Task completed: task_id={task_id}, node_id={node_id}, time_ms={elapsed}')

            return TaskResult(
                task_id=task_id,
                status=TaskStatus(result_payload.get('status', 'completed')),
                result=result_payload.get('result', ''),
                cost=cost,
                node_id=node_id,
                processing_time_ms=result_payload.get('processing_time_ms', elapsed),
            ).model_dump()
        except asyncio.TimeoutError:
            await reputation.record_task_result(node_id, success=False,
                                                response_time_ms=60000)
            await reputation.check_and_suspend(node_id)
            raise HTTPException(status_code=504, detail='Task timed out')
        except HTTPException:
            raise
        except Exception as e:
            if node_id:
                await reputation.record_task_result(node_id, success=False,
                                                    response_time_ms=60000)
                await reputation.check_and_suspend(node_id)
            logger.error(f'Task error: task_id={task_id}, error={e}')
            raise HTTPException(status_code=500, detail='An unexpected error occurred')
        finally:
            if charged and charged_amount > 0:
                credit_service.reward(client_id, charged_amount, f'refund-{task_id}')
                logger.info(f'Refunded {charged_amount} credits to {client_id}')
            app.state.pending_tasks.pop(task_id, None)
    else:
        node_ids = await router.find_nodes_for_split(body.task_type, num_chunks)
        if not node_ids:
            raise HTTPException(status_code=404, detail='No node available for this task type')

        if not credit_service.charge(client_id, cost, task_id):
            raise HTTPException(status_code=402, detail='Insufficient credits')

        charged = True
        charged_amount = cost
        sub_tasks = []

        try:
            futures = []
            for i, chunk in enumerate(chunks):
                sub_task_id = f'{task_id}-{i}'
                node_id = node_ids[i % len(node_ids)]

                future = asyncio.get_event_loop().create_future()
                app.state.pending_tasks[sub_task_id] = future
                futures.append(future)
                sub_tasks.append((sub_task_id, node_id))

                assign_msg = make_ws_msg(MsgType.TASK_ASSIGN, {
                    'task_id': sub_task_id,
                    'task_type': body.task_type,
                    'content': chunk,
                    'params': body.params,
                    'reward': 10,
                })
                success = await ws_manager.send_to_node(node_id, assign_msg)
                if not success:
                    await registry.unregister(node_id)

            # Store task -> node mapping for rating (use first node)
            await reputation.redis.setex(f'gn:task:node:{task_id}', 3600,
                                         node_ids[0])

            start = time.time()
            results = await asyncio.wait_for(
                asyncio.gather(*futures), timeout=60.0)
            elapsed = int((time.time() - start) * 1000)

            result_texts = [r.get('result', '') for r in results]
            merged = router.merge_results(result_texts)

            for sub_task_id, node_id in sub_tasks:
                credit_service.reward(node_id, 10, sub_task_id)

            charged = False  # credits legitimately spent

            # Record reputation for all participating nodes
            for _, node_id in sub_tasks:
                await reputation.record_task_result(node_id, success=True,
                                                    response_time_ms=elapsed)
                await reputation.check_and_suspend(node_id)

            app.state.total_tasks_today += 1
            logger.info(f'Split task completed: task_id={task_id}, chunks={num_chunks}, time_ms={elapsed}')

            return TaskResult(
                task_id=task_id,
                status=TaskStatus.COMPLETED,
                result=merged,
                cost=cost,
                processing_time_ms=elapsed,
            ).model_dump()
        except asyncio.TimeoutError:
            for _, node_id in sub_tasks:
                await reputation.record_task_result(node_id, success=False,
                                                    response_time_ms=60000)
                await reputation.check_and_suspend(node_id)
            raise HTTPException(status_code=504, detail='Task timed out')
        except HTTPException:
            raise
        except Exception as e:
            for _, node_id in sub_tasks:
                await reputation.record_task_result(node_id, success=False,
                                                    response_time_ms=60000)
                await reputation.check_and_suspend(node_id)
            logger.error(f'Split task error: task_id={task_id}, error={e}')
            raise HTTPException(status_code=500, detail='An unexpected error occurred')
        finally:
            if charged and charged_amount > 0:
                credit_service.reward(client_id, charged_amount, f'refund-{task_id}')
                logger.info(f'Refunded {charged_amount} credits to {client_id}')
            for sub_task_id, _ in sub_tasks:
                app.state.pending_tasks.pop(sub_task_id, None)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = 'gemmanet/auto'
    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


def _openai_error(message: str, error_type: str, code: str, status_code: int):
    return JSONResponse(
        status_code=status_code,
        content={'error': {'message': message, 'type': error_type, 'code': code}},
    )


def _parse_model_to_task_type(model: str) -> str:
    model_lower = model.lower().strip()
    if '/' in model_lower:
        capability = model_lower.split('/', 1)[1]
    else:
        capability = model_lower
    mapping = {
        'auto': 'chat',
        'chat': 'chat',
        'translate': 'translate',
        'summarize': 'summarize',
        'code': 'code',
    }
    return mapping.get(capability, 'chat')


def _messages_to_content(messages: list[ChatMessage]) -> str:
    system_parts = [m.content for m in messages if m.role == 'system']
    user_parts = [m.content for m in messages if m.role == 'user']
    last_user = user_parts[-1] if user_parts else ''
    if system_parts:
        return f'System: {system_parts[-1]}\n\nUser: {last_user}'
    return last_user


@app.post('/v1/chat/completions')
@limiter.limit('60/minute')
async def openai_chat_completions(request: Request, body: ChatCompletionRequest,
                                  authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        return _openai_error('Invalid API key', 'authentication_error', 'invalid_api_key', 401)
    key = authorization[7:].strip()
    if not key:
        return _openai_error('Invalid API key', 'authentication_error', 'invalid_api_key', 401)

    key_info = APIKeyManager.validate(key)
    if not key_info:
        return _openai_error('Invalid API key', 'authentication_error', 'invalid_api_key', 401)

    client_id = key_info['node_id']
    credit_service: CreditService = app.state.credit_service
    router: RoutingEngine = app.state.router
    ws_manager: WSConnectionManager = app.state.ws_manager

    task_type = _parse_model_to_task_type(body.model)
    content = _messages_to_content(body.messages)
    task_id = str(uuid.uuid4())
    cost = 10

    balance = credit_service.get_balance(client_id)
    if balance < cost:
        return _openai_error('Insufficient credits', 'billing_error', 'insufficient_credits', 402)

    node_id = await router.find_best_node(task_type)
    if not node_id:
        return _openai_error(
            f'No node available for task type: {task_type}',
            'server_error', 'no_node_available', 503,
        )

    if not credit_service.charge(client_id, cost, task_id):
        return _openai_error('Insufficient credits', 'billing_error', 'insufficient_credits', 402)

    charged = True
    charged_amount = cost

    try:
        future = asyncio.get_event_loop().create_future()
        app.state.pending_tasks[task_id] = future

        params = {}
        if body.max_tokens is not None:
            params['max_tokens'] = body.max_tokens
        if body.temperature is not None:
            params['temperature'] = body.temperature

        assign_msg = make_ws_msg(MsgType.TASK_ASSIGN, {
            'task_id': task_id,
            'task_type': task_type,
            'content': content,
            'params': params,
            'reward': 10,
        })
        success = await ws_manager.send_to_node(node_id, assign_msg)
        if not success:
            registry: NodeRegistry = app.state.registry
            await registry.unregister(node_id)
            node_id = await router.find_best_node(task_type)
            if node_id:
                success = await ws_manager.send_to_node(node_id, assign_msg)
            if not success:
                return _openai_error('No available node', 'server_error', 'no_node_available', 503)

        result_payload = await asyncio.wait_for(future, timeout=60.0)
        credit_service.reward(node_id, 10, task_id)
        charged = False  # credits legitimately spent
        app.state.total_tasks_today += 1

        result_text = result_payload.get('result', '')

        if body.stream:
            async def stream_response():
                words = result_text.split()
                chunk_size = 3
                completion_id = f'chatcmpl-{task_id}'
                for i in range(0, len(words), chunk_size):
                    chunk = ' '.join(words[i:i + chunk_size])
                    if i > 0:
                        chunk = ' ' + chunk
                    data = json.dumps({
                        'id': completion_id,
                        'object': 'chat.completion.chunk',
                        'choices': [{'index': 0, 'delta': {'content': chunk},
                                     'finish_reason': None}],
                    })
                    yield f'data: {data}\n\n'
                    await asyncio.sleep(0.05)
                done_data = json.dumps({
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': {},
                                 'finish_reason': 'stop'}],
                })
                yield f'data: {done_data}\n\n'
                yield 'data: [DONE]\n\n'

            return StreamingResponse(
                stream_response(),
                media_type='text/event-stream',
            )

        return {
            'id': f'chatcmpl-{uuid.uuid4().hex}',
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': body.model,
            'choices': [{
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': result_text,
                },
                'finish_reason': 'stop',
            }],
            'usage': {
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'total_tokens': 0,
            },
        }
    except asyncio.TimeoutError:
        return _openai_error('Request timed out', 'server_error', 'timeout', 504)
    except Exception as e:
        logger.error(f'OpenAI compat error: task_id={task_id}, error={e}')
        return _openai_error('Internal server error', 'server_error', 'internal_error', 500)
    finally:
        if charged and charged_amount > 0:
            credit_service.reward(client_id, charged_amount, f'refund-{task_id}')
            logger.info(f'Refunded {charged_amount} credits to {client_id}')
        app.state.pending_tasks.pop(task_id, None)


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
    return {
        'status': 'running',
        'version': '0.1.0a1',
        'online_nodes': app.state.ws_manager.online_count,
        'total_tasks_today': app.state.total_tasks_today,
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


@app.get('/api/v1/balance')
@limiter.limit('120/minute')
async def get_balance(request: Request, api_key: str = Depends(verify_api_key)):
    credit_service: CreditService = app.state.credit_service
    account = credit_service.get_account(api_key)
    if not account:
        return {'node_id': api_key, 'balance': 0, 'total_earned': 0, 'total_spent': 0}
    return account


@app.get('/api/v1/history')
@limiter.limit('120/minute')
async def get_history(request: Request, api_key: str = Depends(verify_api_key),
                      limit: int = Query(default=20)):
    credit_service: CreditService = app.state.credit_service
    return credit_service.get_transactions(api_key, limit=limit)


@app.post('/api/v1/rate')
@limiter.limit('60/minute')
async def rate_task(request: Request, body: RateBody,
                    api_key: str = Depends(verify_api_key)):
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail='Rating must be 1-5')

    reputation: ReputationSystem = app.state.reputation
    node_id = await reputation.redis.get(f'gn:task:node:{body.task_id}')
    if not node_id:
        raise HTTPException(status_code=404, detail='Task not found or expired')

    await reputation.record_user_rating(node_id, body.rating)
    return {'status': 'rated', 'node_id': node_id, 'rating': body.rating}


@app.get('/api/v1/reputation/{node_id}')
@limiter.limit('120/minute')
async def get_reputation(request: Request, node_id: str):
    reputation: ReputationSystem = app.state.reputation
    stats = await reputation.get_stats(node_id)
    return {'node_id': node_id, **stats}


@app.get('/api/v1/leaderboard')
@limiter.limit('120/minute')
async def get_leaderboard(request: Request, limit: int = Query(default=20)):
    reputation: ReputationSystem = app.state.reputation
    return await reputation.get_leaderboard(limit=limit)


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
