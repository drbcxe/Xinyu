"""Evaluation-only clock injection; normal HTTP requests always use real UTC time."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from app.config import get_settings

_clock: ContextVar[datetime | None] = ContextVar("evaluation_clock", default=None)


def now():
    return _clock.get() or datetime.now(UTC)


@contextmanager
def frozen_clock(moment: datetime):
    if get_settings().app_env not in {"test", "evaluation"} or moment.tzinfo is None:
        raise ValueError("A frozen clock is permitted only in an isolated evaluation")
    token = _clock.set(moment)
    try:
        yield
    finally:
        _clock.reset(token)
