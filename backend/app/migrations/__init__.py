"""Migration cơ sở dữ liệu có đánh số phiên bản (chạy được trên SQLite và PostgreSQL).

Cách hoạt động (gọi tự động trong ``init_db``):
1. ``Base.metadata.create_all`` tạo các bảng MỚI (chưa có) – bảng cũ không bị đụng tới.
2. Từng migration trong ``versions/`` được chạy đúng 1 lần theo thứ tự tên file; mỗi migration tự kiểm tra
   cột/index đã tồn tại hay chưa nên chạy lại không lỗi (idempotent).
3. Phiên bản đã chạy được ghi vào bảng ``schema_migrations``.

Chạy tay:  ``python -m app.migrations``  (in danh sách migration đã/ chưa chạy và chạy phần còn thiếu)
"""
from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from . import versions as _versions


def _all_versions() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(_versions.__path__) if m.name.startswith("m"))


def applied_versions(conn: Connection) -> set[str]:
    if "schema_migrations" not in inspect(conn).get_table_names():
        return set()
    return {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}


def run_migrations(engine: Engine) -> list[str]:
    """Chạy các migration còn thiếu, mỗi migration trong 1 transaction. Trả về danh sách vừa chạy."""
    ran: list[str] = []
    for name in _all_versions():
        with engine.begin() as conn:
            if name in applied_versions(conn):
                continue
            module = importlib.import_module(f"{_versions.__name__}.{name}")
            module.upgrade(conn)
            conn.execute(text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                         {"v": name, "t": datetime.now().isoformat(sep=" ", timespec="seconds")})
            ran.append(name)
    return ran


# ---------------------------------------------------------------- tiện ích dùng trong các migration
def has_column(conn: Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(conn).get_columns(table)}


def add_column(conn: Connection, table: str, column: str, ddl_type: str, default_sql: str | None = None) -> bool:
    """ALTER TABLE ... ADD COLUMN nếu cột chưa có. ``ddl_type`` viết theo SQL chuẩn (VARCHAR, INTEGER, BOOLEAN)."""
    if has_column(conn, table, column):
        return False
    sql = f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
    if default_sql is not None:
        sql += f" DEFAULT {default_sql}"
    conn.execute(text(sql))
    return True


def has_index(conn: Connection, table: str, index: str) -> bool:
    return index in {i["name"] for i in inspect(conn).get_indexes(table)}


def create_index(conn: Connection, name: str, table: str, columns: list[str]) -> bool:
    if has_index(conn, table, name):
        return False
    conn.execute(text(f"CREATE INDEX {name} ON {table} ({', '.join(columns)})"))
    return True
