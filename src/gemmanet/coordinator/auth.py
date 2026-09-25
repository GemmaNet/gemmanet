"""API Key management and Feedback models."""
import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func, or_

from gemmanet.coordinator.database import Base, SessionLocal

LAST_USED_RESOLUTION = timedelta(minutes=5)


class APIKey(Base):
    __tablename__ = 'api_keys'
    id = Column(Integer, primary_key=True)
    key_prefix = Column(String(11), nullable=False)
    key_hash = Column(String(128), nullable=False)
    email = Column(String(256), nullable=True)
    # The DB column keeps its historical name so existing databases need no
    # migration; it identifies the account that owns the key.
    account_id = Column('node_id', String(64), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)


class Feedback(Base):
    __tablename__ = 'feedback'
    id = Column(Integer, primary_key=True)
    account_id = Column('node_id', String(64), nullable=True)
    feedback_type = Column(String(32), nullable=False)
    message = Column(String(4096), nullable=False)
    email = Column(String(256), nullable=True)
    status = Column(String(32), default='new')
    created_at = Column(DateTime, server_default=func.now())


class APIKeyManager:
    @staticmethod
    def generate_key() -> tuple[str, str, str]:
        """Generate a new API key. Returns (raw_key, prefix, key_hash)."""
        raw_key = 'gn_' + secrets.token_hex(16)
        prefix = raw_key[:11]  # gn_ + first 8 hex chars
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return raw_key, prefix, key_hash

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @staticmethod
    def register(email: str | None = None) -> dict:
        raw_key, prefix, key_hash = APIKeyManager.generate_key()
        account_id = str(uuid.uuid4())

        with SessionLocal() as session:
            try:
                api_key = APIKey(
                    key_prefix=prefix,
                    key_hash=key_hash,
                    email=email,
                    account_id=account_id,
                    is_active=True,
                )
                session.add(api_key)
                session.commit()
            except Exception:
                session.rollback()
                raise

        return {'api_key': raw_key, 'account_id': account_id}

    @staticmethod
    def validate(raw_key: str) -> dict | None:
        key_hash = APIKeyManager.hash_key(raw_key)
        with SessionLocal() as session:
            try:
                record = session.query(APIKey).filter_by(
                    key_hash=key_hash, is_active=True
                ).first()
                if not record:
                    return None
                info = {'account_id': record.account_id, 'email': record.email}
                # Touch last_used_at at most every few minutes instead of
                # writing on every authenticated request.
                session.query(APIKey).filter(
                    APIKey.id == record.id,
                    or_(APIKey.last_used_at.is_(None),
                        APIKey.last_used_at < func.now() - LAST_USED_RESOLUTION),
                ).update({APIKey.last_used_at: func.now()}, synchronize_session=False)
                session.commit()
                return info
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def revoke(key_prefix: str) -> bool:
        with SessionLocal() as session:
            try:
                record = session.query(APIKey).filter_by(
                    key_prefix=key_prefix, is_active=True
                ).first()
                if not record:
                    return False
                record.is_active = False
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise
