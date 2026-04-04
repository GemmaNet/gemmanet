"""SQLAlchemy ORM: Account, Transaction tables."""
from sqlalchemy import Column, Integer, String, DateTime, func
from gemmanet.credits.database import Base


class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    node_id = Column(String(64), unique=True, nullable=False)
    balance = Column(Integer, default=1000)
    frozen = Column(Integer, default=0)
    total_earned = Column(Integer, default=0)
    total_spent = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    tx_id = Column(String(64), unique=True, nullable=False)
    from_node = Column(String(64), nullable=True)
    to_node = Column(String(64), nullable=True)
    amount = Column(Integer, nullable=False)
    tx_type = Column(String(32), nullable=False)
    task_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
