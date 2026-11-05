"""Sinh số chứng từ tự động theo tháng: PT-2026-10-0001, PC-..., PN-..., KK-..."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SequenceCounter


def next_number(session: Session, prefix: str, on_date: date) -> str:
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
    return f"{prefix}-{period}-{counter.last_value:04d}"
