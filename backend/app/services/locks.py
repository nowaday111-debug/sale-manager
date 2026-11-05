"""Khóa sổ: khi bảng kê tháng của khách đã CONFIRMED thì không được sửa chứng từ trong tháng đó."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import PeriodLockedError
from ..models import Partner, Statement, StatementStatus


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def ensure_period_open(session: Session, partner_id: int | None, on_date: date) -> None:
    if partner_id is None:
        return
    period = period_of(on_date)
    locked = session.scalar(
        select(Statement.id).where(Statement.partner_id == partner_id, Statement.period == period,
                                   Statement.status == StatementStatus.CONFIRMED)
    )
    if locked:
        partner = session.get(Partner, partner_id)
        raise PeriodLockedError(partner.name if partner else str(partner_id), period)
