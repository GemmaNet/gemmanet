import pytest
from gemmanet.credits.service import CreditService
from gemmanet.credits.database import init_db, engine, Base


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_create_account():
    svc = CreditService()
    acc = svc.create_account('test-node-1')
    assert svc.get_balance('test-node-1') == 1000


def test_charge_and_reward():
    svc = CreditService()
    svc.create_account('sender')
    svc.create_account('receiver')
    assert svc.charge('sender', 100, 'task-1') is True
    assert svc.get_balance('sender') == 900
    assert svc.reward('receiver', 100, 'task-1') is True
    assert svc.get_balance('receiver') == 1100


def test_charge_insufficient():
    svc = CreditService()
    svc.create_account('poor-node', initial_balance=5)
    assert svc.charge('poor-node', 100, 'task-2') is False
    assert svc.get_balance('poor-node') == 5


def test_process_task_payment():
    svc = CreditService()
    svc.create_account('buyer')
    svc.create_account('seller')
    result = svc.process_task_payment('buyer', 'seller', 50, 'task-3')
    assert result is True
    assert svc.get_balance('buyer') == 950
    assert svc.get_balance('seller') == 1050


def test_transactions_history():
    svc = CreditService()
    svc.create_account('node-a')
    svc.charge('node-a', 10, 'task-4')
    svc.reward('node-a', 20, 'task-5')
    txns = svc.get_transactions('node-a')
    assert len(txns) >= 2
