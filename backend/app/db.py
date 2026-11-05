"""Kết nối database, kiểu Decimal chính xác và quản lý transaction."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from sqlalchemy import Numeric, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings
from .money import D


class DecimalType(TypeDecorator):
    """Lưu Decimal chính xác tuyệt đối.

    - SQLite không có kiểu thập phân thật (NUMERIC bị ép về REAL/float) nên lưu dạng chuỗi.
    - PostgreSQL dùng NUMERIC(20, 4) gốc.
    Mọi phép cộng/trừ tiền thực hiện bằng Python Decimal, không cộng trong SQL trên SQLite.
    """

    impl = String(40)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(20, 4, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = D(value)
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, Decimal) else D(value)


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - chạy khi kết nối
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(bind: Engine | None = None) -> None:
    from . import models  # noqa: F401  (đăng ký bảng)
    from .audit import register_audit  # noqa: F401

    from .migrations import run_migrations

    target = bind or engine
    Base.metadata.create_all(bind=target)   # bảng mới
    run_migrations(target)                  # cột / index bổ sung cho bảng cũ (app/migrations/versions)


@contextmanager
def transaction(session: Session) -> Iterator[Session]:
    """Một nghiệp vụ = một transaction: commit khi xong, rollback toàn bộ khi có lỗi."""
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
