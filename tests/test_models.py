import pytest
from gemmanet.sdk.models import (
    TaskRequest, TaskResult, TaskStatus, NodeInfo,
    WSMessage, MsgType, NodeRegisterPayload,
    HeartbeatPayload, TaskAssignPayload, TaskResultPayload,
    make_ws_msg, parse_ws_msg,
)


def test_task_request_creation():
    req = TaskRequest(task_type='echo', content='hello')
    assert req.task_type == 'echo'
    assert req.content == 'hello'
    assert req.params == {}


def test_task_result_creation():
    res = TaskResult(task_id='t1', status=TaskStatus.COMPLETED,
                     result='world', cost=10)
    assert res.status == TaskStatus.COMPLETED
    assert res.cost == 10


def test_node_info():
    node = NodeInfo(node_id='n1', name='test',
                    capabilities=['echo'], languages=['en'])
    assert node.online is True


def test_ws_message_roundtrip():
    raw = make_ws_msg(MsgType.HEARTBEAT, {'node_id': 'n1',
        'active_tasks': 0, 'cpu_percent': 10.0})
    msg = parse_ws_msg(raw)
    assert msg.msg_type == MsgType.HEARTBEAT
    assert msg.payload['node_id'] == 'n1'


def test_node_register_payload():
    p = NodeRegisterPayload(node_id='n1', name='test',
        capabilities=['echo'], languages=['en'])
    assert p.node_id == 'n1'
