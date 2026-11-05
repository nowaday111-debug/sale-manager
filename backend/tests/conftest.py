from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.demo import stargate_t10  # noqa: E402


def _engine():
    # Chạy toàn bộ test trên PostgreSQL:  SALE_TEST_DB_URL=postgresql+psycopg://user@host:5432/db pytest
    pg_url = os.getenv("SALE_TEST_DB_URL")
    if pg_url:
        from sqlalchemy import text

        engine = create_engine(pg_url)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        init_db(engine)
        return engine
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    init_db(engine)
    return engine


@pytest.fixture()
def engine():
    return _engine()


@pytest.fixture()
def session(engine):
    s = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture()
def demo(session):
    return stargate_t10.seed(session)
