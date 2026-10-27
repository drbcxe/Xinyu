"""Versioned, owner-scoped choices; no implicit consent for existing accounts."""

import hashlib
import json
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import RelationshipPrivacy
from app.repositories.database import get_engine

POLICY_VERSION = "2026-10-27.1"


def hostname(value):
    try:
        return urlsplit(value).hostname or "未配置"
    except ValueError:
        return "配置无效"


def model_destinations():
    settings = get_settings()
    return {
        "text": {
            "host": hostname(settings.model_base_url),
            "model": settings.model_name,
        },
        "vision": {
            "host": hostname(
                settings.relationship_vision_base_url or settings.model_base_url
            ),
            "model": settings.relationship_vision_model,
        },
    }


def provider_fingerprint():
    return hashlib.sha256(
        json.dumps(model_destinations(), sort_keys=True).encode()
    ).hexdigest()


def is_accepted(row):
    return bool(
        row
        and row.accepted_at
        and not row.revoked_at
        and row.version == POLICY_VERSION
        and row.provider_fingerprint == provider_fingerprint()
    )


def privacy_status(db: Session, owner: str):
    row = db.get(RelationshipPrivacy, owner, populate_existing=True)
    return {
        "version": POLICY_VERSION,
        "provider_fingerprint": provider_fingerprint(),
        "accepted": is_accepted(row),
        "accepted_at": row.accepted_at if row else None,
        "revoked_at": row.revoked_at if row else None,
        "history_days": row.history_days if row else 90,
        "destinations": model_destinations(),
        "disclosures": [
            "咨询时，你提交的聊天、称呼与表达偏好、最近四轮咨询及检索命中的偏好/聊天片段，会发给已配置的文本模型服务；重排也会发送问题和候选片段。",
            "识别截图时，所选图片会发给视觉模型服务；图片不写入业务数据库，转录确认后才提交分析。",
            "向量检索在本地 Embedding 和当前 Qdrant 中处理；本应用不会把对话或反馈用于训练，也不会自动发送微信消息。模型服务方的留存按其政策执行。",
            "你应只提交有权使用的内容，可先删去身份与敏感信息。随时撤回后停止新模型调用；已发出的请求无法追回，历史内容仍可查看、导出或删除。",
        ],
        "retention_note": "咨询、反馈、导入聊天及终态任务按所选天数在后台清理；已确认偏好、重要事项和约会计划保留到你删除。临时执行检查点最多7天，成功任务通常24小时后清理，容量紧张时可提前移除已完成任务的临时状态，保留咨询结果。安全记录30天、用量明细14天。现有备份和模型服务方副本不属于在线删除范围。",
    }


def require_processing(owner: str, db: Session | None = None):
    # The evaluator owns synthetic-only, random databases and never reads business data.
    if get_settings().app_env == "evaluation":
        return
    if db is None:
        with Session(get_engine()) as check_db:
            require_processing(owner, check_db)
        return
    if not is_accepted(db.get(RelationshipPrivacy, owner, populate_existing=True)):
        raise AppError(
            403,
            "MODEL_CONSENT_REQUIRED",
            "请先阅读并确认本空间的 AI 数据处理说明",
            {"policy_version": POLICY_VERSION},
        )
