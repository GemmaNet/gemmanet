"""Pydantic models: TaskRequest, TaskResult, NodeInfo, etc."""
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from uuid import uuid4
import json


class TaskStatus(str, Enum):
    QUEUED = 'queued'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class TaskRequest(BaseModel):
    task_type: str
    content: str
    params: dict = Field(default_factory=dict)
    max_cost: int | None = None


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    result: str | None = None
    cost: int = 0
    node_id: str | None = None
    processing_time_ms: int = 0


class NodeInfo(BaseModel):
    node_id: str
    name: str
    capabilities: list[str]
    languages: list[str] = []
    online: bool = True
    load: float = 0.0


class CreditBalance(BaseModel):
    node_id: str
    balance: int
    total_earned: int = 0
    total_spent: int = 0


class MsgType(str, Enum):
    NODE_REGISTER = 'node_register'
    HEARTBEAT = 'heartbeat'
    TASK_ASSIGN = 'task_assign'
    TASK_RESULT = 'task_result'
    CREDIT_UPDATE = 'credit_update'
    ERROR = 'error'
    BENCHMARK = 'benchmark'
    BENCHMARK_RESULT = 'benchmark_result'


class WSMessage(BaseModel):
    msg_id: str = Field(default_factory=lambda: str(uuid4()))
    msg_type: MsgType
    payload: dict = Field(default_factory=dict)
    sender_id: str = ''
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class NodeRegisterPayload(BaseModel):
    node_id: str
    name: str
    capabilities: list[str]
    languages: list[str]
    model_info: dict = Field(default_factory=dict)


class HeartbeatPayload(BaseModel):
    node_id: str
    active_tasks: int
    cpu_percent: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TaskAssignPayload(BaseModel):
    task_id: str
    task_type: str
    content: str
    params: dict
    reward: int


class TaskResultPayload(BaseModel):
    task_id: str
    node_id: str
    status: TaskStatus
    result: str
    processing_time_ms: int


class CreditUpdatePayload(BaseModel):
    node_id: str
    balance: int
    change: int
    reason: str


class BenchmarkPayload(BaseModel):
    prompts: list[str]


class BenchmarkResultPayload(BaseModel):
    results: list[dict]


def make_ws_msg(msg_type: MsgType, payload_dict: dict) -> str:
    msg = WSMessage(msg_type=msg_type, payload=payload_dict)
    return msg.model_dump_json()


def parse_ws_msg(raw: str) -> WSMessage:
    return WSMessage.model_validate_json(raw)
