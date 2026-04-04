import pytest
from gemmanet.coordinator.router import RoutingEngine


def test_should_split_short():
    router = RoutingEngine(registry=None, ws_manager=None)
    assert router.should_split('short text', 'translate') is False


def test_should_split_long():
    router = RoutingEngine(registry=None, ws_manager=None)
    long_text = 'word ' * 500
    assert router.should_split(long_text, 'translate') is True


def test_split_content():
    router = RoutingEngine(registry=None, ws_manager=None)
    text = 'Para one.\n\nPara two.\n\nPara three.\n\nPara four.'
    chunks = router.split_content(text, 2)
    assert len(chunks) == 2
    merged = router.merge_results(chunks)
    assert 'Para one' in merged
    assert 'Para four' in merged


def test_merge_results():
    router = RoutingEngine(registry=None, ws_manager=None)
    parts = ['Hello world', 'Second part', 'Third part']
    merged = router.merge_results(parts)
    assert 'Hello world' in merged
    assert 'Third part' in merged
