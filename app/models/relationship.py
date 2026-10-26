from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RelationshipProfile(Base):
    __tablename__ = "relationship_profiles"
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_name: Mapped[str] = mapped_column(String(80), default="伴侣")
    style: Mapped[str] = mapped_column(
        String(500), default="自然、简短，不要油腻或说教"
    )


class RelationshipTurn(Base):
    __tablename__ = "relationship_turns"
    __table_args__ = (UniqueConstraint("owner", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    content: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipItem(Base):
    __tablename__ = "relationship_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    quote: Mapped[str] = mapped_column(Text, default="")
    source_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class RelationshipAccount(Base):
    __tablename__ = "relationship_accounts"
    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipSession(Base):
    __tablename__ = "relationship_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipLoginLimit(Base):
    __tablename__ = "relationship_login_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipJob(Base):
    __tablename__ = "relationship_jobs"
    __table_args__ = (UniqueConstraint("owner", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipNotice(Base):
    __tablename__ = "relationship_notices"
    __table_args__ = (UniqueConstraint("item_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="unread")
    weixin_status: Mapped[str] = mapped_column(String(20), default="not_connected")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipWeixinBinding(Base):
    __tablename__ = "relationship_weixin_bindings"
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(200))
    user_id: Mapped[str] = mapped_column(String(200))
    token_encrypted: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(String(500))
    context_token: Mapped[str] = mapped_column(Text, default="")
    cursor: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="connected")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
