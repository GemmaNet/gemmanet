from gemmanet.coordinator.router import RoutingEngine


def make_router(**kwargs):
    return RoutingEngine(registry=None, ws_manager=None, **kwargs)


def test_should_split_short():
    router = make_router()
    assert router.should_split('short text', 'translate') is False


def test_should_split_long():
    router = make_router()
    long_text = 'word ' * 500
    assert router.should_split(long_text, 'translate') is True


def test_only_whitelisted_tasks_are_split():
    router = make_router()
    long_text = 'word ' * 500
    # A chat prompt or a summary can't be answered piecewise.
    assert router.should_split(long_text, 'chat') is False
    assert router.should_split(long_text, 'summarize') is False


def test_split_whitelist_is_configurable(monkeypatch):
    long_text = 'word ' * 500
    assert make_router(splittable_tasks={'echo'}).should_split(long_text, 'echo') is True
    monkeypatch.setenv('GEMMANET_SPLIT_TASKS', 'translate, proofread')
    router = make_router()
    assert router.should_split(long_text, 'proofread') is True
    assert router.should_split(long_text, 'chat') is False


def test_split_content():
    router = make_router()
    text = 'Para one.\n\nPara two.\n\nPara three.\n\nPara four.'
    chunks = router.split_content(text, 2)
    assert len(chunks) == 2
    merged = router.merge_results(chunks)
    assert 'Para one' in merged
    assert 'Para four' in merged


def test_merge_results():
    router = make_router()
    parts = ['Hello world', 'Second part', 'Third part']
    merged = router.merge_results(parts)
    assert 'Hello world' in merged
    assert 'Third part' in merged
