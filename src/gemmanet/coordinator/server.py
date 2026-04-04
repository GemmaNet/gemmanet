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
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

from gemmanet.coordinator.ws_manager import WSConnectionManager
from gemmanet.coordinator.registry import NodeRegistry
from gemmanet.coordinator.router import RoutingEngine
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

ADMIN_KEY = os.getenv('ADMIN_KEY', 'gemmanet-admin-2026')

limiter = Limiter(key_func=get_remote_address)


class RequestBody(BaseModel):
    task_type: str
    content: str
    params: dict = {}
    max_cost: int | None = None
    api_key: str


class RegisterBody(BaseModel):
    email: Optional[str] = None


class FeedbackBody(BaseModel):
    type: str
    message: str
    email: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    registry = NodeRegistry(redis_url=redis_url)
    await registry.init()
    init_db()

    ws_manager = WSConnectionManager()
    credit_service = CreditService()
    router = RoutingEngine(registry=registry, ws_manager=ws_manager)

    app.state.registry = registry
    app.state.ws_manager = ws_manager
    app.state.credit_service = credit_service
    app.state.router = router
    app.state.pending_tasks = {}
    app.state.total_tasks_today = 0

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


# --- Global exception handler ---

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f'Unhandled error: {exc}', exc_info=True)
    return JSONResponse(
        status_code=500,
        content={'error': 'Internal server error', 'detail': 'An unexpected error occurred'},
    )


# --- Auth dependency ---

async def verify_api_key(authorization: str = Header(...)) -> str:
    """Validate API key and return the associated node_id."""
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
async def list_feedback(request: Request, authorization: str = Header(...)):
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

        while True:
            raw = await websocket.receive_text()
            msg = parse_ws_msg(raw)

            if msg.msg_type == MsgType.HEARTBEAT:
                load_info = msg.payload
                await registry.update_heartbeat(node_id, load_info)

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
    if not key:
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

    if not should_split:
        node_id = await router.find_best_node(body.task_type, body.params)
        if not node_id:
            raise HTTPException(status_code=404, detail='No node available for this task type')

        if not credit_service.charge(client_id, cost, task_id):
            raise HTTPException(status_code=402, detail='Insufficient credits')

        future = asyncio.get_event_loop().create_future()
        app.state.pending_tasks[task_id] = future

        assign_msg = make_ws_msg(MsgType.TASK_ASSIGN, {
            'task_id': task_id,
            'task_type': body.task_type,
            'content': body.content,
            'params': body.params,
            'reward': 10,
        })
        await ws_manager.send_to_node(node_id, assign_msg)

        try:
            start = time.time()
            result_payload = await asyncio.wait_for(future, timeout=60.0)
            elapsed = int((time.time() - start) * 1000)

            credit_service.reward(node_id, 10, task_id)
            app.state.total_tasks_today += 1
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
            credit_service.reward(client_id, cost, task_id)
            raise HTTPException(status_code=504, detail='Task timed out')
        except Exception as e:
            credit_service.reward(client_id, cost, task_id)
            logger.error(f'Task error: task_id={task_id}, error={e}')
            raise HTTPException(status_code=500, detail='An unexpected error occurred')
        finally:
            app.state.pending_tasks.pop(task_id, None)
    else:
        node_ids = await router.find_nodes_for_split(body.task_type, num_chunks)
        if not node_ids:
            raise HTTPException(status_code=404, detail='No node available for this task type')

        if not credit_service.charge(client_id, cost, task_id):
            raise HTTPException(status_code=402, detail='Insufficient credits')

        futures = []
        sub_tasks = []
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
            await ws_manager.send_to_node(node_id, assign_msg)

        try:
            start = time.time()
            results = await asyncio.wait_for(
                asyncio.gather(*futures), timeout=60.0)
            elapsed = int((time.time() - start) * 1000)

            result_texts = [r.get('result', '') for r in results]
            merged = router.merge_results(result_texts)

            rewarded_nodes = set()
            for sub_task_id, node_id in sub_tasks:
                if node_id not in rewarded_nodes:
                    credit_service.reward(node_id, 10, sub_task_id)
                    rewarded_nodes.add(node_id)
                else:
                    credit_service.reward(node_id, 10, sub_task_id)

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
            credit_service.reward(client_id, cost, task_id)
            raise HTTPException(status_code=504, detail='Task timed out')
        except Exception as e:
            credit_service.reward(client_id, cost, task_id)
            logger.error(f'Split task error: task_id={task_id}, error={e}')
            raise HTTPException(status_code=500, detail='An unexpected error occurred')
        finally:
            for sub_task_id, _ in sub_tasks:
                app.state.pending_tasks.pop(sub_task_id, None)


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


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=os.getenv('COORDINATOR_HOST', '0.0.0.0'),
                port=int(os.getenv('COORDINATOR_PORT', '8800')))
