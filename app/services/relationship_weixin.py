"""Optional official-protocol adapter. No private-chat database access.

Protocol: https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md
This adapter is independently implemented against the documented wire format.
"""

import base64
import os
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.errors import AppError
from app.models.relationship import (
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipWeixinBinding,
)
from app.repositories.database import get_engine

API_BASE = "https://ilinkai.weixin.qq.com"
VERSION = "2.4.10"
qr_sessions: dict[str, dict] = {}


def validate_base(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or not (
            url.hostname == "weixin.qq.com" or url.hostname.endswith(".weixin.qq.com")
        )
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        raise AppError(502, "WEIXIN_HOST_INVALID", "微信返回的服务地址不在允许范围")
    return f"https://{url.hostname}"


def cipher() -> Fernet:
    folder = PROJECT_ROOT / ".runtime" / "secrets"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "weixin.key"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def headers(token: str | None = None):
    result = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": str((2 << 16) | (4 << 8) | 10),
    }
    if token:
        result["Authorization"] = "Bearer " + token
    return result


def request_api(
    base: str, path: str, token: str | None, payload: dict, timeout: float = 15
):
    if token:
        payload = {
            **payload,
            "base_info": {"channel_version": VERSION, "bot_agent": "Xinyu/0.1.0"},
        }
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            response = client.post(
                validate_base(base) + path, headers=headers(token), json=payload
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        raise AppError(
            502, "WEIXIN_NETWORK_ERROR", "微信请求未确认成功，请检查网络后重试"
        ) from None
    if not isinstance(body, dict):
        raise AppError(502, "WEIXIN_INVALID_RESPONSE", "微信响应格式异常")
    return body


def start_login(owner: str):
    body = request_api(
        API_BASE, "/ilink/bot/get_bot_qrcode?bot_type=3", None, {"local_token_list": []}
    )
    if not body.get("qrcode") or not body.get("qrcode_img_content"):
        raise AppError(502, "WEIXIN_QR_ERROR", "未取得可用微信二维码")
    qr_sessions[owner] = {
        "qrcode": body["qrcode"],
        "base": API_BASE,
        "started": datetime.now(UTC),
    }
    return {"qr_content": body["qrcode_img_content"], "status": "wait"}


def poll_login(db: Session, owner: str, verify_code: str = ""):
    session = qr_sessions.get(owner)
    if not session or (datetime.now(UTC) - session["started"]).total_seconds() > 300:
        raise AppError(409, "WEIXIN_QR_EXPIRED", "请重新获取二维码")
    params = {"qrcode": session["qrcode"]}
    if verify_code:
        params["verify_code"] = verify_code
    try:
        with httpx.Client(trust_env=False, timeout=15) as client:
            response = client.get(
                session["base"] + "/ilink/bot/get_qrcode_status",
                params=params,
                headers={
                    "iLink-App-Id": "bot",
                    "iLink-App-ClientVersion": str((2 << 16) | (4 << 8) | 10),
                },
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        raise AppError(
            502, "WEIXIN_NETWORK_ERROR", "微信授权状态暂时无法获取"
        ) from None
    status = body.get("status", "wait")
    if status == "scaned_but_redirect" and body.get("redirect_host"):
        session["base"] = validate_base(
            "https://" + str(body["redirect_host"]).removeprefix("https://")
        )
    if status == "confirmed":
        if not all(
            body.get(key) for key in ("bot_token", "ilink_bot_id", "ilink_user_id")
        ):
            raise AppError(502, "WEIXIN_LOGIN_INVALID", "微信授权缺少必要字段")
        binding = db.get(RelationshipWeixinBinding, owner)
        if not binding:
            binding = RelationshipWeixinBinding(owner=owner)
            db.add(binding)
        binding.bot_id, binding.user_id = body["ilink_bot_id"], body["ilink_user_id"]
        binding.token_encrypted = cipher().encrypt(body["bot_token"].encode()).decode()
        binding.base_url = validate_base(body.get("baseurl", API_BASE))
        binding.context_token, binding.cursor, binding.status = "", "", "connected"
        binding.updated_at = datetime.now(UTC)
        db.commit()
        qr_sessions.pop(owner, None)
    return {"status": status}


def send(
    binding: RelationshipWeixinBinding, text: str, client_id: str
) -> tuple[str, str]:
    if not binding.context_token:
        return "needs_activity", "请先在微信给助手发一条消息，再重试投递"
    token = cipher().decrypt(binding.token_encrypted.encode()).decode()
    try:
        result = request_api(
            binding.base_url,
            "/ilink/bot/sendmessage",
            token,
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": binding.user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": binding.context_token,
                    "item_list": [{"type": 1, "text_item": {"text": text[:3500]}}],
                }
            },
        )
    except AppError:
        return "uncertain", "未收到微信成功确认；为避免重复发送，没有自动重发"
    if result.get("ret", 0) != 0 or result.get("errcode", 0) != 0:
        return "needs_activity", "微信拒绝投递；会话可能过期，请先发消息给助手后重试"
    return "accepted", ""


def deliver_notices():
    with Session(get_engine()) as db:
        notice_ids = db.scalars(
            select(RelationshipNotice.id)
            .where(
                RelationshipNotice.status == "unread",
                RelationshipNotice.weixin_status == "not_connected",
            )
            .limit(20)
        ).all()
    for notice_id in notice_ids:
        with Session(get_engine()) as db:
            snapshot = db.get(RelationshipNotice, notice_id)
            if not snapshot:
                continue
            # Same order as item edits: item, then notice. Cancellation cannot race delivery.
            item = db.scalar(
                select(RelationshipItem)
                .where(RelationshipItem.id == snapshot.item_id)
                .with_for_update()
            )
            notice = db.scalar(
                select(RelationshipNotice)
                .where(
                    RelationshipNotice.id == notice_id,
                    RelationshipNotice.status == "unread",
                    RelationshipNotice.weixin_status == "not_connected",
                )
                .with_for_update(skip_locked=True)
            )
            if not notice:
                continue
            binding = db.scalar(
                select(RelationshipWeixinBinding)
                .where(RelationshipWeixinBinding.owner == notice.owner)
                .with_for_update()
            )
            if not item or item.status != "active" or item.revision != notice.revision:
                notice.status, notice.weixin_status = "cancelled", "cancelled"
            elif binding and binding.status == "connected":
                notice.weixin_status, notice.error = send(
                    binding, "心语提醒：" + notice.title, notice.id
                )
            db.commit()


def poll_bindings():
    # Optional channel remains idle until the user explicitly completes QR authorization.
    from app.services.relationship_jobs import enqueue

    with Session(get_engine()) as db:
        owners = db.scalars(
            select(RelationshipWeixinBinding.owner).where(
                RelationshipWeixinBinding.status == "connected"
            )
        ).all()
    for owner in owners:
        with Session(get_engine()) as db:
            binding = db.get(RelationshipWeixinBinding, owner)
            if not binding or binding.status != "connected":
                continue
            token = cipher().decrypt(binding.token_encrypted.encode()).decode()
            try:
                body = request_api(
                    binding.base_url,
                    "/ilink/bot/getupdates",
                    token,
                    {"get_updates_buf": binding.cursor},
                    timeout=8,
                )
            except AppError:
                continue
            if body.get("ret", 0) != 0 or body.get("errcode", 0) != 0:
                if -14 in (body.get("ret"), body.get("errcode")):
                    binding.status = "expired"
                    db.commit()
                continue
            for msg in body.get("msgs", []):
                if (
                    msg.get("message_type") != 1
                    or msg.get("from_user_id") != binding.user_id
                    or msg.get("group_id")
                ):
                    continue
                binding.context_token = (
                    msg.get("context_token") or binding.context_token
                )
                text = "\n".join(
                    str(item.get("text_item", {}).get("text", ""))
                    for item in msg.get("item_list", [])
                    if item.get("type") == 1
                )
                if not text.strip():
                    continue
                rid = str(
                    uuid5(
                        NAMESPACE_URL,
                        binding.bot_id
                        + ":"
                        + str(msg.get("message_id") or msg.get("client_id")),
                    )
                )
                job = enqueue(db, owner, rid, text[:16000])
                job.payload = {**job.payload, "channel": "weixin"}
                db.commit()
            binding.cursor = body.get("get_updates_buf") or binding.cursor
            binding.updated_at = datetime.now(UTC)
            db.commit()
            for job in db.scalars(
                select(RelationshipJob).where(
                    RelationshipJob.owner == owner,
                    RelationshipJob.status == "succeeded",
                )
            ).all():
                if job.payload.get("channel") != "weixin" or job.payload.get(
                    "delivery"
                ):
                    continue
                from app.models.relationship import RelationshipTurn

                turn = db.get(RelationshipTurn, job.turn_id)
                if turn:
                    result = turn.result
                    answer = (
                        result.get("analysis", "")
                        + "\n\n"
                        + "\n\n".join(result.get("replies", []))
                    )
                    status, _ = send(binding, answer, job.id)
                    job.payload = {**job.payload, "delivery": status}
            db.commit()
