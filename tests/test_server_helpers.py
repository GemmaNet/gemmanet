"""Coordinator request validation and protocol helpers (no services needed)."""
import pytest
from pydantic import ValidationError

from gemmanet.coordinator.server import (
    RequestBody,
    _load_info,
    _parse_model_to_task_type,
    _usage,
    derive_node_id,
)


def test_node_id_is_stable_per_account_and_name():
    assert derive_node_id('acct-1', 'echo') == derive_node_id('acct-1', 'echo')
    assert derive_node_id('acct-1', 'echo') != derive_node_id('acct-2', 'echo')
    assert derive_node_id('acct-1', 'echo') != derive_node_id('acct-1', 'echo2')


@pytest.mark.parametrize('model, task_type', [
    ('gemmanet/auto', 'chat'),
    ('gemmanet/chat', 'chat'),
    ('gemmanet/translate', 'translate'),
    ('gemmanet/echo', 'echo'),        # any capability, not just a fixed list
    ('gpt-4o-mini', 'chat'),          # tools that keep a default model still work
])
def test_parse_model_to_task_type(model, task_type):
    assert _parse_model_to_task_type(model) == task_type


@pytest.mark.parametrize('key', ['content', 'not-an-identifier', '1x'])
def test_request_params_must_be_handler_kwargs(key):
    with pytest.raises(ValidationError):
        RequestBody(task_type='echo', content='x', params={key: 1})


def test_request_body_has_no_credit_fields():
    body = RequestBody(task_type='echo', content='x', params={'prefix': 'P'})
    assert 'max_cost' not in body.model_dump()


def test_heartbeat_load_info_is_sanitized():
    assert _load_info({'cpu_percent': 'nan', 'active_tasks': -3,
                       'name': '<script>'}) == {'cpu_percent': 0.0, 'active_tasks': 0}
    assert _load_info({'cpu_percent': 250}) == {'cpu_percent': 100.0, 'active_tasks': 0}


def test_usage_is_normalized():
    assert _usage({}) is None
    assert _usage({'usage': {'prompt_tokens': 3, 'completion_tokens': '2'}}) == {
        'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}


def test_chat_message_text_ignores_malformed_parts():
    from gemmanet.coordinator.server import ChatMessage

    msg = ChatMessage(role='user', content=[
        {'type': 'text', 'text': 'hello '},
        {'type': 'text', 'text': None},
        {'type': 'image_url', 'image_url': {'url': 'x'}},
        {'type': 'text', 'text': 'world'},
    ])
    assert msg.text() == 'hello world'
    assert ChatMessage(role='user', content=None).text() == ''
