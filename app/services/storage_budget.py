"""Atomic PostgreSQL accounting. Content counters and reservations are maintained by triggers."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.relationship import RelationshipStorageTotal
from app.repositories.database import get_engine

LOCK_SQL = "SELECT pg_advisory_xact_lock(718302612)"


def lock_storage(db):
    """Take before explicit row locks; never keep it across model/vector/SMTP calls."""
    db.execute(text(LOCK_SQL))


def configure_storage(force_rebuild=False):
    settings = get_settings()
    settings.storage_reserve_covers_execution()
    with get_engine().begin() as conn:
        conn.execute(text(LOCK_SQL))
        conn.execute(
            text("""INSERT INTO public.relationship_storage_policy(id,owner_bytes,global_bytes,graph_thread_bytes,job_reserve_bytes,profile_limit,emergency_bytes,owner_vector_points,global_vector_points)
            VALUES(1,:owner,:global_,:graph,:reserve,:profiles,:emergency,:owner_points,:global_points)
            ON CONFLICT(id) DO UPDATE SET owner_bytes=EXCLUDED.owner_bytes,global_bytes=EXCLUDED.global_bytes,graph_thread_bytes=EXCLUDED.graph_thread_bytes,job_reserve_bytes=EXCLUDED.job_reserve_bytes,profile_limit=EXCLUDED.profile_limit,emergency_bytes=EXCLUDED.emergency_bytes,owner_vector_points=EXCLUDED.owner_vector_points,global_vector_points=EXCLUDED.global_vector_points"""),
            {
                "owner": settings.owner_storage_bytes,
                "global_": settings.global_storage_bytes,
                "graph": settings.graph_thread_storage_bytes,
                "reserve": settings.job_storage_reserve_bytes,
                "profiles": settings.global_profile_limit,
                "emergency": settings.storage_emergency_bytes,
                "owner_points": settings.owner_vector_points,
                "global_points": settings.global_vector_points,
            },
        )
        installed = conn.scalar(text("SELECT public.xinyu_storage_install()"))
        if installed or force_rebuild:
            conn.execute(text("SELECT public.xinyu_storage_rebuild()"))


def storage_summary(db, owner):
    values = dict(
        db.execute(
            select(
                RelationshipStorageTotal.category, RelationshipStorageTotal.bytes
            ).where(RelationshipStorageTotal.scope == owner)
        ).all()
    )
    reserved = values.pop("reserved", 0)
    headroom = min(
        get_settings().storage_emergency_bytes, get_settings().owner_storage_bytes // 20
    )
    return {
        "used_bytes": sum(values.values()),
        "reserved_bytes": reserved,
        "limit_bytes": get_settings().owner_storage_bytes,
        "remaining_bytes": max(
            0,
            get_settings().owner_storage_bytes
            - sum(values.values())
            - reserved
            - headroom,
        ),
        "by_category": values,
        "system_headroom_bytes": headroom,
        "task_reserve_bytes": get_settings().job_storage_reserve_bytes,
        "note": "按业务行的逻辑 JSON 字节记账，含对话、资料、任务、账号元数据及图状态；索引、向量、WAL、日志和备份另行监控。",
    }


def global_storage_summary():
    with Session(get_engine()) as db:
        values = dict(
            db.execute(
                select(
                    RelationshipStorageTotal.category, RelationshipStorageTotal.bytes
                ).where(RelationshipStorageTotal.scope == "global")
            ).all()
        )
    reserved = values.pop("reserved", 0)
    return {
        "used_bytes": sum(values.values()),
        "reserved_bytes": reserved,
        "limit_bytes": get_settings().global_storage_bytes,
        "by_category": values,
    }


def capacity_status():
    """Protected ops evidence: logical budget is distinct from physical allocation."""
    import shutil

    from app.config import PROJECT_ROOT
    from app.services.physical_storage import runtime_file_status
    from app.services.vector_capacity import vector_summary

    result = global_storage_summary()
    try:
        result["runtime_files"] = runtime_file_status()
    except (OSError, RuntimeError):
        result["runtime_files"] = {"unavailable": True}
    with Session(get_engine()) as db:
        result["vectors"] = vector_summary(db)
    with get_engine().connect() as conn:
        result["postgres_database_bytes"] = conn.scalar(
            text("SELECT pg_database_size(current_database())")
        )
    disk = shutil.disk_usage(PROJECT_ROOT)
    result["workspace_disk"] = {"total_bytes": disk.total, "free_bytes": disk.free}
    result["note"] = (
        "逻辑预算不等于磁盘占用；数据库大小含索引及内部开销，"
        "不含外部 WAL、Qdrant、模型缓存、日志和备份。工作区磁盘数据仅代表工作区所在文件系统。"
    )
    return result


def pressure_cleanup(db, owner, needed=0):
    """Drop completed execution buffers when needed; all user-visible turns remain."""
    db.execute(text(LOCK_SQL))
    summary = storage_summary(db, owner)
    emergency = min(
        get_settings().storage_emergency_bytes, get_settings().owner_storage_bytes // 20
    )
    if (
        summary["used_bytes"] + summary["reserved_bytes"] + needed
        <= summary["limit_bytes"] - emergency
    ):
        return
    threads = list(
        db.scalars(
            text("""SELECT t.thread_id FROM public.relationship_storage_threads t JOIN public.relationship_jobs j
        ON t.thread_id='xinyu:'||j.owner||':'||j.id WHERE t.owner=:owner AND j.status='succeeded' ORDER BY j.updated_at LIMIT 100"""),
            {"owner": owner},
        )
    )
    from app.services.graph_checkpoints import delete_checkpoint_thread

    for thread in threads:
        delete_checkpoint_thread(thread, db=db)
        summary = storage_summary(db, owner)
        if (
            summary["used_bytes"] + summary["reserved_bytes"] + needed
            <= summary["limit_bytes"] - emergency
        ):
            break
