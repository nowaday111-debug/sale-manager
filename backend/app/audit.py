"""Ghi nhật ký kiểm toán tự động cho mọi INSERT/UPDATE/DELETE (trừ bảng hệ thống).

Ghi cùng connection, cùng transaction với nghiệp vụ: rollback thì nhật ký cũng rollback.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event, insert, inspect
from sqlalchemy.orm import Session

from .config import settings

_SKIP_TABLES = {"audit_log", "sequences", "inventory_transactions", "schema_migrations"}


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _snapshot(obj) -> dict:
    state = inspect(obj)
    return {attr.key: _jsonable(attr.value) for attr in state.attrs if attr.key in state.mapper.columns}


def _before_values(obj) -> dict:
    state = inspect(obj)
    result = {}
    for attr in state.attrs:
        if attr.key not in state.mapper.columns:
            continue
        hist = attr.history
        if hist.has_changes():
            result[attr.key] = _jsonable(hist.deleted[0]) if hist.deleted else None
        else:
            result[attr.key] = _jsonable(attr.value)
    return result


def _pk(obj) -> str | None:
    ident = inspect(obj).identity
    if not ident:
        return None
    return "/".join(str(x) for x in ident)


def _after_flush(session: Session, _ctx) -> None:
    from .auth import current_user

    user = session.info.get("user") or current_user.get() or settings.default_user
    rows = []
    for obj in session.new:
        table = getattr(obj, "__tablename__", None)
        if table and table not in _SKIP_TABLES:
            rows.append(dict(table_name=table, record_id=_pk(obj), action="INSERT",
                             before_json=None, after_json=json.dumps(_snapshot(obj), ensure_ascii=False),
                             user=user, at=datetime.now()))
    for obj in session.dirty:
        table = getattr(obj, "__tablename__", None)
        if not table or table in _SKIP_TABLES or not session.is_modified(obj, include_collections=False):
            continue
        rows.append(dict(table_name=table, record_id=_pk(obj), action="UPDATE",
                         before_json=json.dumps(_before_values(obj), ensure_ascii=False),
                         after_json=json.dumps(_snapshot(obj), ensure_ascii=False),
                         user=user, at=datetime.now()))
    for obj in session.deleted:
        table = getattr(obj, "__tablename__", None)
        if table and table not in _SKIP_TABLES:
            rows.append(dict(table_name=table, record_id=_pk(obj), action="DELETE",
                             before_json=json.dumps(_before_values(obj), ensure_ascii=False),
                             after_json=None, user=user, at=datetime.now()))
    if rows:
        from .models import AuditLog

        session.connection().execute(insert(AuditLog.__table__), rows)


def register_audit() -> None:
    if not event.contains(Session, "after_flush", _after_flush):
        event.listen(Session, "after_flush", _after_flush)


register_audit()
