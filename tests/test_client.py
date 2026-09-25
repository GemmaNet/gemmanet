"""Client SDK error mapping."""
import httpx
import pytest

from gemmanet import Client, GemmaNetError, NoNodeAvailableError, TaskTimeoutError


def client_returning(status: int, body: dict) -> Client:
    client = Client(api_key='gn_test', coordinator_url='http://coordinator')
    client._client = httpx.Client(
        base_url='http://coordinator',
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    return client


def test_request_404_means_no_node():
    client = client_returning(404, {'detail': 'No node available for this task type'})
    with pytest.raises(NoNodeAvailableError):
        client.request('echo', 'x')


def test_request_504_is_timeout():
    with pytest.raises(TaskTimeoutError):
        client_returning(504, {'detail': 'Task timed out'}).request('echo', 'x')


def test_rating_unknown_task_is_not_a_routing_error():
    client = client_returning(404, {'detail': 'Task not found or expired'})
    with pytest.raises(GemmaNetError, match='Task not found') as excinfo:
        client.rate('nope', 5)
    assert not isinstance(excinfo.value, NoNodeAvailableError)
