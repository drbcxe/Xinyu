from functools import lru_cache

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.repositories.database import get_engine


@lru_cache
def get_pool() -> ConnectionPool:
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    pool = ConnectionPool(
        url.render_as_string(hide_password=False),
        min_size=1,
        max_size=6,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=agent_checkpoints",
            "connect_timeout": 10,
        },
    )
    pool.open(wait=True)
    return pool


def initialize_checkpointer() -> None:
    with get_engine().begin() as connection:
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS agent_checkpoints")
    PostgresSaver(get_pool()).setup()


@lru_cache
def get_graph():
    from app.graph.graphs.incoming import build_graph

    return build_graph(PostgresSaver(get_pool()))


def close_checkpointer() -> None:
    get_graph.cache_clear()
    if get_pool.cache_info().currsize:
        get_pool().close()
        get_pool.cache_clear()
