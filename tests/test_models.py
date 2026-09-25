import pytest
from pydantic import ValidationError

from gemmanet.sdk.models import (
    Completion,
    MsgType,
    NodeInfo,
    NodeRegisterPayload,
    TaskRequest,
    TaskResult,
    TaskStatus,
    make_ws_msg,
    parse_ws_msg,
)


def test_task_request_creation():
    req = TaskRequest(task_type='echo', content='hello')
    assert req.task_type == 'echo'
    assert req.content == 'hello'
    assert req.params == {}


def test_task_result_creation():
    res = TaskResult(task_id='t1', status=TaskStatus.COMPLETED, result='world')
    assert res.status == TaskStatus.COMPLETED
    assert res.usage is None
    assert 'cost' not in res.model_dump()


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


def test_credit_message_type_removed():
    assert 'CREDIT_UPDATE' not in MsgType.__members__


def test_node_register_payload():
    p = NodeRegisterPayload(api_key='gn_x', name='test',
                            capabilities=['echo'], languages=['en'])
    assert p.capabilities == ['echo']


@pytest.mark.parametrize('capabilities', [
    [],                       # at least one capability
    ['<script>'],             # markup is not a capability name
    ['has space'],
    ['x' * 65],
])
def test_node_register_payload_rejects_bad_capabilities(capabilities):
    with pytest.raises(ValidationError):
        NodeRegisterPayload(api_key='gn_x', name='test', capabilities=capabilities)


def test_node_register_payload_limits_name_and_model_info():
    with pytest.raises(ValidationError):
        NodeRegisterPayload(api_key='gn_x', name='n' * 65, capabilities=['echo'])
    with pytest.raises(ValidationError):
        NodeRegisterPayload(api_key='gn_x', name='n', capabilities=['echo'],
                            model_info={'blob': 'x' * 5000})


def test_completion_is_a_string_with_usage():
    c = Completion('hello', usage={'prompt_tokens': 3, 'completion_tokens': 1})
    assert c == 'hello'
    assert c.upper() == 'HELLO'
    assert c.usage['prompt_tokens'] == 3
    assert Completion('x').usage is None
