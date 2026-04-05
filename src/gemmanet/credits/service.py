"""Credit operations: charge, reward, balance, freeze."""
import uuid

from gemmanet.credits.database import SessionLocal
from gemmanet.credits.models import Account, Transaction


class CreditService:
    def __init__(self):
        pass

    def create_account(self, node_id: str, initial_balance: int = 1000) -> Account:
        with SessionLocal() as session:
            try:
                existing = session.query(Account).filter_by(node_id=node_id).first()
                if existing:
                    return existing
                account = Account(node_id=node_id, balance=initial_balance)
                session.add(account)
                session.flush()
                tx = Transaction(
                    tx_id=str(uuid.uuid4()),
                    to_node=node_id,
                    amount=initial_balance,
                    tx_type='register_bonus',
                )
                session.add(tx)
                session.commit()
                session.refresh(account)
                return account
            except Exception:
                session.rollback()
                raise

    def get_balance(self, node_id: str) -> int:
        with SessionLocal() as session:
            try:
                account = session.query(Account).filter_by(node_id=node_id).first()
                return account.balance if account else 0
            except Exception:
                session.rollback()
                return 0

    def get_account(self, node_id: str) -> dict | None:
        with SessionLocal() as session:
            try:
                account = session.query(Account).filter_by(node_id=node_id).first()
                if not account:
                    return None
                return {
                    'node_id': account.node_id,
                    'balance': account.balance,
                    'frozen': account.frozen,
                    'total_earned': account.total_earned,
                    'total_spent': account.total_spent,
                }
            except Exception:
                session.rollback()
                return None

    def charge(self, from_node: str, amount: int, task_id: str) -> bool:
        with SessionLocal() as session:
            try:
                account = session.query(Account).filter_by(node_id=from_node).first()
                if not account or account.balance < amount:
                    return False
                account.balance -= amount
                account.total_spent += amount
                tx = Transaction(
                    tx_id=str(uuid.uuid4()),
                    from_node=from_node,
                    amount=amount,
                    tx_type='task_payment',
                    task_id=task_id,
                )
                session.add(tx)
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def reward(self, to_node: str, amount: int, task_id: str) -> bool:
        with SessionLocal() as session:
            try:
                account = session.query(Account).filter_by(node_id=to_node).first()
                if not account:
                    account = Account(node_id=to_node, balance=0)
                    session.add(account)
                    session.flush()
                account.balance += amount
                account.total_earned += amount
                tx = Transaction(
                    tx_id=str(uuid.uuid4()),
                    to_node=to_node,
                    amount=amount,
                    tx_type='task_reward',
                    task_id=task_id,
                )
                session.add(tx)
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def freeze(self, node_id: str, amount: int) -> bool:
        with SessionLocal() as session:
            try:
                account = session.query(Account).filter_by(node_id=node_id).first()
                if not account or account.balance < amount:
                    return False
                account.balance -= amount
                account.frozen += amount
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def unfreeze(self, node_id: str, amount: int) -> bool:
        with SessionLocal() as session:
            try:
                account = session.query(Account).filter_by(node_id=node_id).first()
                if not account or account.frozen < amount:
                    return False
                account.frozen -= amount
                account.balance += amount
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise

    def get_transactions(self, node_id: str, limit: int = 20) -> list[dict]:
        with SessionLocal() as session:
            try:
                txns = (session.query(Transaction)
                        .filter((Transaction.from_node == node_id) |
                                (Transaction.to_node == node_id))
                        .order_by(Transaction.created_at.desc())
                        .limit(limit)
                        .all())
                return [{
                    'tx_id': t.tx_id,
                    'from_node': t.from_node,
                    'to_node': t.to_node,
                    'amount': t.amount,
                    'tx_type': t.tx_type,
                    'task_id': t.task_id,
                    'created_at': str(t.created_at) if t.created_at else None,
                } for t in txns]
            except Exception:
                session.rollback()
                return []

    def process_task_payment(self, from_node: str, to_node: str,
                             amount: int, task_id: str) -> bool:
        with SessionLocal() as session:
            try:
                sender = session.query(Account).filter_by(node_id=from_node).first()
                if not sender or sender.balance < amount:
                    return False
                receiver = session.query(Account).filter_by(node_id=to_node).first()
                if not receiver:
                    receiver = Account(node_id=to_node, balance=0)
                    session.add(receiver)
                    session.flush()

                sender.balance -= amount
                sender.total_spent += amount
                receiver.balance += amount
                receiver.total_earned += amount

                tx1 = Transaction(
                    tx_id=str(uuid.uuid4()),
                    from_node=from_node,
                    to_node=to_node,
                    amount=amount,
                    tx_type='task_payment',
                    task_id=task_id,
                )
                session.add(tx1)
                session.commit()
                return True
            except Exception:
                session.rollback()
                raise
