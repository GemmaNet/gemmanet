"""Pydantic models: TaskRequest, TaskResult, NodeInfo, etc."""
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field, StringConstraints, field_validator

CapabilityName = Annotated[str, StringConstraints(
    pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
LanguageCode = Annotated[str, StringConstraints(min_length=1, max_length=16)]


class TaskStatus(str, Enum):
    QUEUED = 'queued'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class TaskRequest(BaseModel):
    task_type: str
    content: str
    params: dict = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    result: str | None = None
    node_id: str | None = None
    processing_time_ms: int = 0
    usage: dict | None = None


class NodeInfo(BaseModel):
    node_id: str
    name: str
    capabilities: list[str]
    languages: list[str] = []
    online: bool = True
    load: float = 0.0


class MsgType(str, Enum):
    NODE_REGISTER = 'node_register'
    NODE_REGISTERED = 'node_registered'
    HEARTBEAT = 'heartbeat'
    TASK_ASSIGN = 'task_assign'
    TASK_CHUNK = 'task_chunk'
    TASK_RESULT = 'task_result'
    ERROR = 'error'
    BENCHMARK = 'benchmark'
    BENCHMARK_RESULT = 'benchmark_result'


class WSMessage(BaseModel):
    msg_id: str = Field(default_factory=lambda: str(uuid4()))
    msg_type: MsgType
    payload: dict = Field(default_factory=dict)
    sender_id: str = ''
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NodeRegisterPayload(BaseModel):
    api_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    capabilities: list[CapabilityName] = Field(min_length=1, max_length=32)
    languages: list[LanguageCode] = Field(default_factory=list, max_length=64)
    model_info: dict = Field(default_factory=dict)

    @field_validator('model_info')
    @classmethod
    def _limit_model_info(cls, value: dict) -> dict:
        if len(json.dumps(value, default=str)) > 2048:
            raise ValueError('model_info must serialize to at most 2048 bytes')
        return value


class NodeRegisteredPayload(BaseModel):
    node_id: str


class HeartbeatPayload(BaseModel):
    node_id: str
    active_tasks: int
    cpu_percent: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskAssignPayload(BaseModel):
    task_id: str
    task_type: str
    content: str
    params: dict
    stream: bool = False


class TaskChunkPayload(BaseModel):
    task_id: str
    delta: str


class TaskResultPayload(BaseModel):
    task_id: str
    node_id: str
    status: TaskStatus
    result: str
    processing_time_ms: int
    usage: dict | None = None


class BenchmarkPayload(BaseModel):
    prompts: list[str]


class BenchmarkResultPayload(BaseModel):
    results: list[dict]


class Completion(str):
    """A handler result string that can also carry token usage.

    Handlers may return ``Completion(text, usage={'prompt_tokens': ..,
    'completion_tokens': ..})`` so the OpenAI-compatible endpoint can report
    real token counts. It behaves exactly like ``str`` everywhere else.
    """
    usage: dict | None

    def __new__(cls, text: str, usage: dict | None = None):
        obj = super().__new__(cls, text)
        obj.usage = usage
        return obj


def make_ws_msg(msg_type: MsgType, payload_dict: dict) -> str:
    msg = WSMessage(msg_type=msg_type, payload=payload_dict)
    return msg.model_dump_json()


def parse_ws_msg(raw: str) -> WSMessage:
    return WSMessage.model_validate_json(raw)
