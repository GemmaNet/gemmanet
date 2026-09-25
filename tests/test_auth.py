"""API keys (PostgreSQL) and database URL handling."""
import pytest

from gemmanet.coordinator.auth import APIKey, APIKeyManager
from gemmanet.coordinator.database import SessionLocal, init_db, normalize_database_url


@pytest.fixture(autouse=True, scope='module')
def db():
    init_db()


def test_normalize_database_url():
    assert normalize_database_url('postgresql://u:p@h/db') == 'postgresql+psycopg2://u:p@h/db'
    assert normalize_database_url('postgres://u:p@h/db') == 'postgresql+psycopg2://u:p@h/db'
    assert normalize_database_url('postgresql+psycopg2://h/db') == 'postgresql+psycopg2://h/db'
    assert normalize_database_url('sqlite:///x.db') == 'sqlite:///x.db'


def test_register_and_validate():
    reg = APIKeyManager.register(email='someone@example.com')
    assert reg['api_key'].startswith('gn_')
    assert 'balance' not in reg
    info = APIKeyManager.validate(reg['api_key'])
    assert info == {'account_id': reg['account_id'], 'email': 'someone@example.com'}
    assert APIKeyManager.validate('gn_not_a_real_key') is None


def test_revoke():
    reg = APIKeyManager.register()
    assert APIKeyManager.revoke(reg['api_key'][:11]) is True
    assert APIKeyManager.validate(reg['api_key']) is None


def test_validate_touches_last_used_at_once_per_window():
    reg = APIKeyManager.register()

    def last_used():
        with SessionLocal() as session:
            return session.query(APIKey).filter_by(account_id=reg['account_id']).one().last_used_at

    assert last_used() is None
    APIKeyManager.validate(reg['api_key'])
    first = last_used()
    assert first is not None
    APIKeyManager.validate(reg['api_key'])
    assert last_used() == first  # no write within the 5-minute window
