"""DB engine, session factory, init_db()."""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()


def normalize_database_url(url: str) -> str:
    """Pin bare PostgreSQL URLs to the psycopg2 driver we depend on.

    SQLAlchemy 2.1 changed the default driver for ``postgresql://`` to
    psycopg 3, which is not installed, so a bare URL would fail to connect.
    """
    for prefix in ('postgresql://', 'postgres://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg2://' + url[len(prefix):]
    return url


DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL environment variable is required. '
                       'Set it in .env file or environment.')
engine = create_engine(normalize_database_url(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def init_db():
    Base.metadata.create_all(bind=engine)
