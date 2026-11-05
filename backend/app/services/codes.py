"""Mã chứng từ quy trình đơn hàng: SO-YYYYMM-XXXX, PO-..., DN-..., DR-... (đánh số lại mỗi tháng)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SequenceCounter

ROLES_APPROVE = {"ADMIN", "MANAGER", "CHIEF_ACCOUNTANT"}
ROLES_ACCOUNTING = {"ADMIN", "MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT"}


def next_code(session: Session, prefix: str, on_date: date) -> str:
    period = f"{on_date.year:04d}-{on_date.month:02d}"
    counter = session.execute(
        select(SequenceCounter).where(SequenceCounter.name == prefix, SequenceCounter.period == period)
        .with_for_update()
    ).scalar_one_or_none()
    if counter is None:
        counter = SequenceCounter(name=prefix, period=period, last_value=0)
        session.add(counter)
    counter.last_value += 1
    session.flush()
    return f"{prefix}-{on_date.year:04d}{on_date.month:02d}-{counter.last_value:04d}"


def warn(session: Session, message: str) -> None:
    session.info.setdefault("warnings", []).append(message)


def pop_warnings(session: Session) -> list[str]:
    return session.info.pop("warnings", [])
