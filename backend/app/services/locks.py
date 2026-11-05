"""Khóa sổ: khi bảng kê tháng của khách đã CONFIRMED thì không được sửa chứng từ trong tháng đó."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import PeriodLockedError
from ..models import Partner, Statement, StatementStatus, period_for


def period_of(d: date, cut_day: int | None = None) -> str:
    return period_for(d, cut_day)


def period_range(period: str, cut_day: int | None = None) -> tuple[date, date]:
    """Ngày đầu – ngày cuối của kỳ. cut_day=25: kỳ 2026-10 = 26/09/2026 → 25/10/2026."""
    import calendar
    from datetime import timedelta

    from ..errors import BusinessError
    try:
        y, m = (int(x) for x in period.split("-"))
        first = date(y, m, 1)
    except (ValueError, TypeError) as exc:
        raise BusinessError(f"Kỳ phải dạng YYYY-MM: {period}") from exc
    if cut_day and 1 <= cut_day < 28:
        end = date(y, m, cut_day)
        prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
        start = date(prev_y, prev_m, cut_day) + timedelta(days=1)
        return start, end
    return first, date(y, m, calendar.monthrange(y, m)[1])


def partner_period(session: Session, partner_id: int | None, d: date) -> str:
    """Kỳ bảng kê theo khách (khách chốt ngày 25 thì 26/09 thuộc kỳ 2026-10)."""
    partner = session.get(Partner, partner_id) if partner_id else None
    return period_for(d, partner.statement_cut_day if partner else None)


def ensure_period_open(session: Session, partner_id: int | None, on_date: date) -> None:
    if partner_id is None:
        return
    period = partner_period(session, partner_id, on_date)
    locked = session.scalar(
        select(Statement.id).where(Statement.partner_id == partner_id, Statement.period == period,
                                   Statement.status == StatementStatus.CONFIRMED)
    )
    if locked:
        partner = session.get(Partner, partner_id)
        raise PeriodLockedError(partner.name if partner else str(partner_id), period)
