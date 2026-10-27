from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    database_url = get_settings().database_url.strip()

    if not database_url:
        raise RuntimeError("请在 .env 中配置 DATABASE_URL")

    return create_engine(
        database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": get_settings().database_connect_timeout,
            "options": f"-c statement_timeout={get_settings().database_statement_timeout_ms}",
        },
        pool_timeout=5,
    )


def get_db() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
