"""API Key management and Feedback models."""
import secrets
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Boolean, func

from gemmanet.credits.database import Base, SessionLocal
from gemmanet.credits.service import CreditService


class APIKey(Base):
    __tablename__ = 'api_keys'
    id = Column(Integer, primary_key=True)
    key_prefix = Column(String(11), nullable=False)
    key_hash = Column(String(128), nullable=False)
    email = Column(String(256), nullable=True)
    node_id = Column(String(64), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)


class Feedback(Base):
    __tablename__ = 'feedback'
    id = Column(Integer, primary_key=True)
    node_id = Column(String(64), nullable=True)
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
    def register(email: str = None) -> dict:
        raw_key, prefix, key_hash = APIKeyManager.generate_key()
        node_id = str(uuid.uuid4())

        with SessionLocal() as session:
            try:
                api_key = APIKey(
                    key_prefix=prefix,
                    key_hash=key_hash,
                    email=email,
                    node_id=node_id,
                    is_active=True,
                )
                session.add(api_key)
                session.commit()
            except Exception:
                session.rollback()
                raise

        credit_service = CreditService()
        credit_service.create_account(node_id, initial_balance=1000)

        return {
            'api_key': raw_key,
            'node_id': node_id,
            'balance': 1000,
        }

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
                record.last_used_at = datetime.utcnow()
                session.commit()
                return {'node_id': record.node_id, 'email': record.email}
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
