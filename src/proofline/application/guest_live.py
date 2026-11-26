"""Admit guest Live using the same PostgreSQL transaction that records the run."""

from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from proofline.application.errors import ApplicationError
from proofline.persistence.models import AnalysisRun, Case

_DAY = timedelta(hours=24)
_COOLDOWN = timedelta(seconds=65)
_ATTEMPTS_PER_GUEST = 3
_RESERVED_TOKENS_PER_ATTEMPT = 8_000
_SHARED_DAY_TOKENS = 160_000
_SHARED_ATTEMPTS = _SHARED_DAY_TOKENS // _RESERVED_TOKENS_PER_ATTEMPT


def _deny(reason: str, remaining: timedelta) -> None:
    raise ApplicationError(
        "GUEST_LIVE_RATE_LIMITED",
        "Guest Live is temporarily limited. Select Replay or try again later.",
        status_code=429,
        details={"limit": reason, "retry_after_seconds": max(1, ceil(remaining.total_seconds()))},
    )


def admit_guest_live(session: Session, guest_id: str) -> datetime:
    # Serialise admissions across guests. The lock lasts through the running-row insert
    # and commit, and is released before the provider is called.
    session.execute(select(func.pg_advisory_xact_lock(0x50524F4F, 5)))
    now: datetime = session.execute(select(func.clock_timestamp())).scalar_one()
    accounted_at = func.coalesce(AnalysisRun.finished_at, AnalysisRun.started_at)
    attempts = session.execute(
        select(Case.guest_id, AnalysisRun.status, accounted_at)
        .join_from(AnalysisRun, Case, Case.id == AnalysisRun.case_id)
        .where(
            AnalysisRun.mode == "live",
            Case.guest_id.is_not(None),
            or_(AnalysisRun.status == "running", accounted_at > now - _DAY),
        )
        .order_by(accounted_at)
    ).all()
    if any(status == "running" for _, status, _ in attempts):
        _deny("busy", _COOLDOWN)

    # A failed or interrupted attempt still reserves its full allowance. Using the
    # completion time accounts for the last part of any delayed provider response.
    own = [at for owner, _, at in attempts if owner == guest_id]
    if len(own) >= _ATTEMPTS_PER_GUEST:
        _deny("guest_day", own[-_ATTEMPTS_PER_GUEST] + _DAY - now)
    if len(attempts) >= _SHARED_ATTEMPTS:
        _deny("shared_day", attempts[-_SHARED_ATTEMPTS][2] + _DAY - now)
    if attempts:
        wait = attempts[-1][2] + _COOLDOWN - now
        if wait > timedelta(0):
            _deny("cooldown", wait)
    return now
