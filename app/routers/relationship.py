"""Account-scoped relationship workflows and optional channel setup."""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipFeedback,
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipProfile,
    RelationshipRagIndex,
    RelationshipTurn,
    RelationshipWeixinBinding,
)
from app.repositories.database import get_db
from app.schemas.relationship import (
    ChatInput,
    ImageInput,
    ItemInput,
    ItemUpdate,
    ProfileInput,
)
from app.services.account_closure import purge_content
from app.services.auth_cache import consume_rate
from app.services.relationship_auth import Owner, check_origin
from app.services.relationship_jobs import enqueue, get_profile, job_data
from app.services.relationship_rag import index_status, retrieve, schedule_index
from app.services.relationship_reads import (
    item_counts,
    item_page,
    jobs_summary,
    presence,
    snapshot_version,
)
from app.services.relationship_vision import transcribe
from app.services.resource_control import (
    admit_request,
    check_storage,
    local_operation_rate,
    operation_scope,
    usage_summary,
)

router = APIRouter(
    prefix="/relationship", tags=["relationship"], dependencies=[Depends(check_origin)]
)
DB = Annotated[Session, Depends(get_db)]


def turn_data(turn):
    return {
        key: getattr(turn, key) for key in ("id", "content", "result", "created_at")
    }


def item_data(item):
    return {
        key: getattr(item, key)
        for key in (
            "id",
            "kind",
            "title",
            "quote",
            "source_turn_id",
            "due_at",
            "status",
            "created_at",
            "revision",
            "details",
        )
    }


def lock_owner(db, owner):
    get_profile(db, owner)
    profile = db.scalar(
        select(RelationshipProfile)
        .where(RelationshipProfile.owner == owner)
        .with_for_update()
    )
    if not profile:
        raise AppError(401, "SPACE_CLOSED", "原账号空间已注销，请重新打开页面")
    return profile


def invalidate_notices(db, item_id):
    for notice in db.scalars(
        select(RelationshipNotice).where(
            RelationshipNotice.item_id == item_id, RelationshipNotice.status == "unread"
        )
    ).all():
        notice.status = "cancelled"


@router.get("/state")
def state(db: DB, owner: Owner):
    profile = get_profile(db, owner)
    version = snapshot_version(db, owner)
    turns = db.scalars(
        select(RelationshipTurn)
        .where(RelationshipTurn.owner == owner)
        .order_by(RelationshipTurn.created_at.desc(), RelationshipTurn.id.desc())
        .limit(21)
    ).all()
    notices = db.scalars(
        select(RelationshipNotice)
        .where(
            RelationshipNotice.owner == owner, RelationshipNotice.status != "cancelled"
        )
        .order_by(RelationshipNotice.created_at.desc())
        .limit(50)
    ).all()
    account = db.scalar(
        select(RelationshipAccount).where(RelationshipAccount.owner == owner)
    )
    binding = db.get(RelationshipWeixinBinding, owner)
    return {
        "version": version,
        "profile": {"partner_name": profile.partner_name, "style": profile.style},
        "turns": [turn_data(t) for t in reversed(turns[:20])],
        "history_has_more": len(turns) > 20,
        "item_counts": item_counts(db, owner),
        "jobs": jobs_summary(db, owner),
        "notices": [
            {
                k: getattr(n, k)
                for k in (
                    "id",
                    "title",
                    "status",
                    "weixin_status",
                    "error",
                    "created_at",
                )
            }
            for n in notices
        ],
        "account": {"username": account.username if account else None},
        "worker_online": presence(),
        "weixin": {
            "status": binding.status if binding else "not_connected",
            "interface_ready": True,
        },
        "supports_images": bool(get_settings().relationship_vision_model.strip()),
        "server_time": datetime.now(UTC),
    }


@router.get("/state/summary")
def state_summary(db: DB, owner: Owner):
    return {
        "version": snapshot_version(db, owner),
        "jobs": jobs_summary(db, owner),
        "worker_online": presence(),
        "server_time": datetime.now(UTC),
    }


@router.put("/profile")
def save_profile(payload: ProfileInput, db: DB, owner: Owner):
    profile = lock_owner(db, owner)
    profile.partner_name, profile.style = (
        payload.partner_name.strip() or "伴侣",
        payload.style.strip() or "自然、简短",
    )
    db.commit()
    return {"ok": True}


@router.post("/transcribe")
def image_transcript(payload: ImageInput, request: Request, db: DB, owner: Owner):
    admit_request(db, owner, request.client.host if request.client else "local", "ocr")
    db.commit()
    db.rollback()
    with operation_scope(owner, str(uuid4()), "ocr"):
        result = transcribe(payload.images)
    return {"transcript": result, "requires_confirmation": True}


@router.post("/runs", status_code=202)
@router.post("/chat", status_code=202)
def start_run(payload: ChatInput, request: Request, db: DB, owner: Owner):
    if not payload.content.strip():
        raise AppError(422, "EMPTY_MESSAGE", "请输入聊天内容")
    job = enqueue(
        db,
        owner,
        str(payload.request_id),
        payload.content.strip(),
        payload.mode,
        ip_scope=request.client.host if request.client else "local",
    )
    request.state.job_id = job.id
    return job_data(job)


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, db: DB, owner: Owner):
    job = db.get(RelationshipJob, str(run_id))
    if not job or job.owner != owner:
        raise AppError(404, "RUN_NOT_FOUND", "任务不存在")
    turn = db.get(RelationshipTurn, job.turn_id) if job.turn_id else None
    return {**job_data(job), "turn": turn_data(turn) if turn else None}


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: UUID, request: Request, db: DB, owner: Owner):
    lock_owner(db, owner)
    consume_rate("retry:" + owner, 10, 60, "任务重试过于频繁，请稍后再试")
    job = db.scalar(
        select(RelationshipJob)
        .where(RelationshipJob.id == str(run_id), RelationshipJob.owner == owner)
        .with_for_update()
    )
    if not job:
        raise AppError(404, "RUN_NOT_FOUND", "任务不存在")
    if job.status != "failed":
        raise AppError(409, "RUN_NOT_FAILED", "只有失败任务可以重试")
    settings = get_settings()
    db.execute(text("SELECT pg_advisory_xact_lock(718302611)"))
    mine = db.scalar(
        select(func.count())
        .select_from(RelationshipJob)
        .where(
            RelationshipJob.owner == owner,
            RelationshipJob.status.in_(["queued", "running"]),
        )
    )
    total = db.scalar(
        select(func.count())
        .select_from(RelationshipJob)
        .where(RelationshipJob.status.in_(["queued", "running"]))
    )
    if mine >= settings.owner_pending_limit or total >= settings.global_queue_limit:
        raise AppError(
            429, "QUEUE_FULL", "请等待已有任务完成后重试", {"retry_after_seconds": 10}
        )
    admit_request(
        db, owner, request.client.host if request.client else "local", "retry"
    )
    job.status, job.error, job.updated_at = "queued", "", datetime.now(UTC)
    job.cancel_requested, job.available_at = False, datetime.now(UTC)
    job.lease_token, job.lease_expires_at = None, None
    db.commit()
    return job_data(job)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: UUID, db: DB, owner: Owner):
    lock_owner(db, owner)
    job = db.scalar(
        select(RelationshipJob)
        .where(RelationshipJob.id == str(run_id), RelationshipJob.owner == owner)
        .with_for_update()
    )
    if not job:
        raise AppError(404, "RUN_NOT_FOUND", "任务不存在")
    if job.status in {"queued", "running"}:
        job.cancel_requested, job.status, job.error = (
            True,
            "cancelled",
            "任务已取消；已发出的模型请求可能仍产生用量",
        )
        job.lease_token, job.lease_expires_at, job.updated_at = (
            None,
            None,
            datetime.now(UTC),
        )
        db.commit()
    return job_data(job)


@router.get("/usage")
def usage(db: DB, owner: Owner):
    return usage_summary(db, owner)


@router.get("/history")
def history(
    db: DB,
    owner: Owner,
    before: Annotated[datetime | None, Query()] = None,
    before_id: UUID | None = None,
):
    query = select(RelationshipTurn).where(RelationshipTurn.owner == owner)
    if before:
        query = (
            query.where(
                tuple_(RelationshipTurn.created_at, RelationshipTurn.id)
                < (before, str(before_id))
            )
            if before_id
            else query.where(RelationshipTurn.created_at < before)
        )
    return [
        turn_data(t)
        for t in reversed(
            db.scalars(
                query.order_by(
                    RelationshipTurn.created_at.desc(), RelationshipTurn.id.desc()
                ).limit(50)
            ).all()
        )
    ]


@router.get("/items")
def list_items(
    db: DB,
    owner: Owner,
    kind: Literal["memory", "reminder", "date_plan", "chat_log"],
    cursor: str | None = Query(default=None, max_length=1000),
    limit: int = Query(default=40, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    status: Literal["all", "active", "completed", "cancelled"] = "all",
):
    return item_page(db, owner, kind, cursor, limit, q, status)


@router.get("/items/{item_id}/content")
def item_content(item_id: UUID, request: Request, db: DB, owner: Owner):
    local_operation_rate(
        owner, request.client.host if request.client else "local", "read-content", 30
    )
    item = db.scalar(
        select(RelationshipItem).where(
            RelationshipItem.id == str(item_id), RelationshipItem.owner == owner
        )
    )
    if not item or item.kind != "chat_log":
        raise AppError(404, "ITEM_NOT_FOUND", "资料不存在")
    return {
        "id": item.id,
        "title": item.title,
        "text": str(item.details.get("text", "")),
    }


@router.post("/items")
def create_item(payload: ItemInput, db: DB, owner: Owner):
    lock_owner(db, owner)
    if not payload.title.strip():
        raise AppError(422, "EMPTY_TITLE", "内容不能为空")
    if payload.kind == "reminder" and (
        not payload.due_at or payload.due_at <= datetime.now(UTC)
    ):
        raise AppError(422, "INVALID_DUE_TIME", "请选择未来的提醒时间")
    details = {}
    if payload.source_turn_id:
        turn = db.get(RelationshipTurn, str(payload.source_turn_id))
        if not turn or turn.owner != owner:
            raise AppError(404, "SOURCE_NOT_FOUND", "来源对话不存在")
        if payload.kind == "date_plan":
            options = turn.result.get("date_plans", [])
            if payload.plan_index is None or payload.plan_index >= len(options):
                raise AppError(422, "PLAN_NOT_FOUND", "请选择有效的约会方案")
            details = options[payload.plan_index]
        elif not payload.quote or payload.quote not in turn.content:
            raise AppError(422, "INVALID_QUOTE", "来源引用与已确认的聊天文字不一致")
        existing = db.scalar(
            select(RelationshipItem).where(
                RelationshipItem.owner == owner,
                RelationshipItem.source_turn_id == str(payload.source_turn_id),
                RelationshipItem.kind == payload.kind,
                RelationshipItem.title == payload.title.strip(),
                RelationshipItem.status == "active",
                RelationshipItem.due_at
                == (payload.due_at if payload.kind == "reminder" else None),
            )
        )
        if existing:
            return item_data(existing)
    elif payload.kind == "date_plan":
        raise AppError(422, "PLAN_SOURCE_REQUIRED", "请从规划结果中保存约会方案")
    check_storage(
        db,
        owner,
        added_bytes=len(
            json.dumps(
                {"title": payload.title, "quote": payload.quote, "details": details}
            ).encode()
        ),
        added_items=1,
    )
    item = RelationshipItem(
        id=str(uuid4()),
        owner=owner,
        kind=payload.kind,
        title=payload.title.strip(),
        quote=payload.quote,
        source_turn_id=str(payload.source_turn_id) if payload.source_turn_id else None,
        due_at=payload.due_at if payload.kind == "reminder" else None,
        status="active",
        created_at=datetime.now(UTC),
        revision=1,
        details=details,
    )
    db.add(item)
    schedule_index(db, item)
    db.commit()
    return item_data(item)


@router.put("/items/{item_id}")
def update_item(item_id: UUID, payload: ItemUpdate, db: DB, owner: Owner):
    lock_owner(db, owner)
    item = db.scalar(
        select(RelationshipItem)
        .where(RelationshipItem.id == str(item_id), RelationshipItem.owner == owner)
        .with_for_update()
    )
    if not item:
        raise AppError(404, "ITEM_NOT_FOUND", "事项不存在")
    if not payload.title.strip():
        raise AppError(422, "EMPTY_TITLE", "内容不能为空")
    check_storage(
        db,
        owner,
        added_bytes=max(0, len(payload.title.encode()) - len(item.title.encode())),
    )
    if item.kind == "reminder" and not payload.due_at:
        raise AppError(422, "MISSING_DUE_TIME", "提醒时间不能为空")
    if (
        item.kind == "reminder"
        and payload.status == "active"
        and payload.due_at != item.due_at
        and payload.due_at <= datetime.now(UTC)
    ):
        raise AppError(422, "INVALID_DUE_TIME", "改期请选择未来时间")
    invalidate_notices(db, item.id)
    item.title, item.status = payload.title.strip(), payload.status
    item.due_at = payload.due_at if item.kind == "reminder" else None
    item.revision += 1
    schedule_index(db, item)
    db.commit()
    return item_data(item)


@router.delete("/items/{item_id}")
def delete_item(item_id: UUID, db: DB, owner: Owner):
    lock_owner(db, owner)
    item = db.scalar(
        select(RelationshipItem)
        .where(RelationshipItem.id == str(item_id), RelationshipItem.owner == owner)
        .with_for_update()
    )
    if not item:
        raise AppError(404, "ITEM_NOT_FOUND", "事项不存在")
    invalidate_notices(db, item.id)
    schedule_index(db, item, deleted=True)
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.post("/notices/{notice_id}/read")
def read_notice(notice_id: UUID, db: DB, owner: Owner):
    notice = db.get(RelationshipNotice, str(notice_id))
    if not notice or notice.owner != owner:
        raise AppError(404, "NOTICE_NOT_FOUND", "提醒记录不存在")
    notice.status = "read"
    db.commit()
    return {"ok": True}


@router.post("/notices/{notice_id}/retry")
def retry_notice(notice_id: UUID, db: DB, owner: Owner):
    notice = db.get(RelationshipNotice, str(notice_id))
    if not notice or notice.owner != owner:
        raise AppError(404, "NOTICE_NOT_FOUND", "提醒记录不存在")
    if notice.weixin_status == "accepted":
        raise AppError(409, "ALREADY_DELIVERED", "微信已接受该消息，不重复发送")
    notice.weixin_status, notice.status = "not_connected", "unread"
    db.commit()
    return {"ok": True}


@router.post("/imports")
async def import_chat(
    request: Request, db: DB, owner: Owner, file: Annotated[UploadFile, File()]
):
    local_operation_rate(
        owner, request.client.host if request.client else "local", "imports", 10
    )
    lock_owner(db, owner)
    raw = await file.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise AppError(413, "IMPORT_TOO_LARGE", "单个聊天文件最大 1 MB")
    if not (file.filename or "").lower().endswith((".txt", ".json")):
        raise AppError(422, "IMPORT_TYPE", "请上传 UTF-8 编码的 TXT 或 JSON 导出文件")
    try:
        text = raw.decode("utf-8-sig")
        if (file.filename or "").lower().endswith(".json"):
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (UnicodeError, ValueError, RecursionError):
        raise AppError(
            422, "IMPORT_INVALID", "文件不是有效的 UTF-8 文本或 JSON"
        ) from None
    if not text.strip():
        raise AppError(422, "IMPORT_EMPTY", "文件内容为空")
    if len(text.encode()) > 1024 * 1024:
        raise AppError(413, "IMPORT_TOO_LARGE", "展开后的聊天文本最大 1 MB")
    check_storage(
        db,
        owner,
        added_bytes=len(json.dumps({"text": text}).encode())
        + len((file.filename or "聊天记录").encode()),
        added_items=1,
    )
    item = RelationshipItem(
        id=str(uuid4()),
        owner=owner,
        kind="chat_log",
        title=(file.filename or "聊天记录")[:500],
        quote="",
        source_turn_id=None,
        due_at=None,
        status="active",
        created_at=datetime.now(UTC),
        revision=1,
        details={"text": text},
    )
    db.add(item)
    schedule_index(db, item)
    db.commit()
    return {"id": item.id, "title": item.title, "characters": len(text)}


@router.delete("/history")
def clear_history(db: DB, owner: Owner):
    lock_owner(db, owner)
    purge_content(db, owner)
    db.commit()
    return {"ok": True}


class RetrievalInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


@router.get("/knowledge")
def knowledge(db: DB, owner: Owner):
    return index_status(db, owner)


@router.post("/knowledge/search")
def knowledge_search(payload: RetrievalInput, request: Request, db: DB, owner: Owner):
    local_operation_rate(
        owner, request.client.host if request.client else "local", "search", 30
    )
    # Retrieval preview stays local and does not consume a generation-model call.
    return retrieve(db, owner, payload.query, top_k=8, rerank=False)


@router.post("/knowledge/reindex")
def knowledge_reindex(request: Request, db: DB, owner: Owner):
    local_operation_rate(
        owner, request.client.host if request.client else "local", "reindex", 2, 300
    )
    lock_owner(db, owner)
    count = 0
    for item in db.scalars(
        select(RelationshipItem).where(
            RelationshipItem.owner == owner,
            RelationshipItem.kind.in_(["memory", "chat_log"]),
            RelationshipItem.status == "active",
        )
    ):
        schedule_index(db, item)
        count += 1
    db.commit()
    return {"queued": count}


@router.post("/knowledge/{item_id}/retry")
def retry_index(item_id: UUID, request: Request, db: DB, owner: Owner):
    local_operation_rate(
        owner, request.client.host if request.client else "local", "index-retry", 10
    )
    job = db.scalar(
        select(RelationshipRagIndex)
        .where(
            RelationshipRagIndex.item_id == str(item_id),
            RelationshipRagIndex.owner == owner,
        )
        .with_for_update()
    )
    if not job:
        raise AppError(404, "INDEX_NOT_FOUND", "索引任务不存在")
    if job.status == "indexing":
        raise AppError(409, "INDEX_BUSY", "资料正在建立索引，请稍后查看")
    item = db.get(RelationshipItem, str(item_id))
    if not item:
        job.status, job.error, job.attempts = "deleting", "", 0
    else:
        schedule_index(db, item)
    db.commit()
    return {"ok": True}


@router.get("/export")
def export_data(db: DB, owner: Owner):
    from fastapi.encoders import jsonable_encoder

    profile = get_profile(db, owner)
    return JSONResponse(
        jsonable_encoder(
            {
                "product": "Xinyu",
                "exported_at": datetime.now(UTC),
                "profile": {
                    "partner_name": profile.partner_name,
                    "style": profile.style,
                },
                "turns": [
                    turn_data(t)
                    for t in db.scalars(
                        select(RelationshipTurn).where(RelationshipTurn.owner == owner)
                    ).all()
                ],
                "items": [
                    item_data(i)
                    for i in db.scalars(
                        select(RelationshipItem).where(RelationshipItem.owner == owner)
                    ).all()
                ],
                "feedback": [
                    {
                        key: getattr(row, key)
                        for key in (
                            "turn_id",
                            "rating",
                            "category",
                            "comment",
                            "created_at",
                            "updated_at",
                        )
                    }
                    for row in db.scalars(
                        select(RelationshipFeedback).where(
                            RelationshipFeedback.owner == owner
                        )
                    )
                ],
            }
        ),
        headers={
            "Content-Disposition": 'attachment; filename="xinyu-export.json"',
            "Cache-Control": "no-store",
        },
    )


def require_account(db: Session, owner: str):
    if not db.scalar(
        select(RelationshipAccount).where(RelationshipAccount.owner == owner)
    ):
        raise AppError(401, "LOGIN_REQUIRED", "请先注册或登录账号，再连接微信")


@router.post("/weixin/login")
def weixin_login(db: DB, owner: Owner):
    from app.services.relationship_weixin import start_login

    require_account(db, owner)
    return start_login(owner)


class VerifyCode(BaseModel):
    code: str = Field(default="", max_length=20)


@router.post("/weixin/login/status")
def weixin_login_status(payload: VerifyCode, db: DB, owner: Owner):
    from app.services.relationship_weixin import poll_login

    require_account(db, owner)
    return poll_login(db, owner, payload.code)


@router.delete("/weixin")
def disconnect_weixin(db: DB, owner: Owner):
    binding = db.get(RelationshipWeixinBinding, owner)
    if binding:
        db.delete(binding)
        db.commit()
    return {"ok": True}
