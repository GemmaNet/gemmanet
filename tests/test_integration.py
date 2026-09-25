"""Integration tests for GemmaNet platform."""
import pytest

from gemmanet import Client, Node, NodeInfo, TaskResult


def test_sdk_imports():
    assert Node is not None
    assert Client is not None
    assert TaskResult is not None
    assert NodeInfo is not None


def test_node_creation():
    node = Node(name='test', capabilities=['echo'], api_key='gn_test')
    assert node.name == 'test'
    assert node.capabilities == ['echo']
    assert node.node_id is None  # assigned by the coordinator on registration


def test_node_requires_api_key(monkeypatch):
    monkeypatch.delenv('GEMMANET_API_KEY', raising=False)
    with pytest.raises(ValueError, match='api_key'):
        Node(name='test', capabilities=['echo'])


def test_node_api_key_from_env(monkeypatch):
    monkeypatch.setenv('GEMMANET_API_KEY', 'gn_from_env')
    assert Node(name='test', capabilities=['echo']).api_key == 'gn_from_env'


def test_client_creation():
    client = Client(api_key='test-key')
    assert client.api_key == 'test-key'
    assert not hasattr(client, 'balance')
    assert not hasattr(client, 'history')
    client.close()
