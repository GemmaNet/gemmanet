"""Integration tests for GemmaNet platform."""
from gemmanet import Node, Client, TaskResult, NodeInfo


def test_sdk_imports():
    assert Node is not None
    assert Client is not None
    assert TaskResult is not None
    assert NodeInfo is not None


def test_node_creation():
    node = Node(name='test', capabilities=['echo'])
    assert node.name == 'test'
    assert node.capabilities == ['echo']


def test_client_creation():
    client = Client(api_key='test-key')
    assert client.api_key == 'test-key'
